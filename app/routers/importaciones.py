from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from typing import Optional
import pandas as pd
import io
import json
import uuid

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext
from app.models.domain import OrdenProduccion, MaestroSKU, Tenant, Linea

router = APIRouter(prefix="/api/lite/importaciones", tags=["Importación y Exportación (UI)"])

# ==========================================
# HELPER: GUARDIA DE INTEGRACIÓN & PLANTA
# ==========================================
def verificar_permiso_carga_y_linea(db: Session, context: TenantContext, linea_id: str) -> Linea:
    tenant = db.exec(select(Tenant).where(Tenant.id == context.tenant_id)).first()
    if tenant and tenant.origen_maestros == "ERP":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Operación denegada. Tu empresa está configurada para recibir datos exclusivamente desde el ERP."
        )
    
    if not context.sub_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe seleccionar una Planta en el menú superior antes de subir archivos."
        )

    # Validar que la línea pertenezca a la planta activa
    linea = db.exec(
        select(Linea).where(
            Linea.id == linea_id,
            Linea.planta_id == context.sub_tenant_id,
            Linea.tenant_id == context.tenant_id
        )
    ).first()

    if not linea:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La línea seleccionada no pertenece a la planta activa o no existe."
        )
    return linea

# ==========================================
# 1. IMPORTACIÓN DE PLAN DE PRODUCCIÓN
# ==========================================
@router.get("/plan/template", status_code=status.HTTP_200_OK)
def descargar_template_plan():
    df = pd.DataFrame(columns=["id_orden", "sku_fk", "cantidad_esperada", "plan_fecha"])
    df.loc[0] = ["OP-EJEMPLO-01", "SKU-123", "500", "2024-12-31"]
    
    stream = io.StringIO()
    df.to_csv(stream, index=False)
    
    response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=template_plan_produccion.csv"
    return response

@router.post("/plan/upload", status_code=status.HTTP_200_OK)
async def subir_plan(
    linea_id: str = Form(..., description="ID de la línea destino"),
    file: UploadFile = File(...),
    map_json: Optional[str] = Form(None, description='Opcional. Ej: {"Tango_ID": "id_orden"}'), 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    # 1. Validaciones Core (ERP & Planta)
    verificar_permiso_carga_y_linea(db, context, linea_id)

    # 2. Leer archivo
    contenido = await file.read()
    try:
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python')
        elif file.filename.lower().endswith(('.xls', '.xlsx')):
            df = pd.read_excel(io.BytesIO(contenido))
        else:
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa CSV o Excel.")

    # 3. Lógica de Mapeo Dinámico
    origen_auditoria = "CSV_TEMPLATE"
    if map_json:
        try:
            mapeo = json.loads(map_json)
            df = df.rename(columns=mapeo)
            origen_auditoria = "CSV_MAPPED"
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="El map_json provisto no es un JSON válido.")

    columnas_requeridas = {"id_orden", "sku_fk", "cantidad_esperada", "plan_fecha"}
    if not columnas_requeridas.issubset(df.columns):
        faltantes = columnas_requeridas - set(df.columns)
        raise HTTPException(status_code=400, detail=f"Faltan columnas requeridas: {faltantes}")

    skus_validos = set(db.exec(select(MaestroSKU.codigo_sku).where(MaestroSKU.tenant_id == context.tenant_id)).all())
    
    creadas, actualizadas = 0, 0
    errores = []

    # 4. Upsert (Forzando la línea desde el Form)
    for index, row in df.iterrows():
        try:
            orden_id = str(row["id_orden"]).strip()
            sku = str(row["sku_fk"]).strip()
            cant = int(row["cantidad_esperada"])
            fecha = str(row["plan_fecha"]).strip()

            if sku not in skus_validos:
                errores.append(f"Fila {index}: El SKU '{sku}' no existe en la base de datos.")
                continue

            orden_db = db.exec(
                select(OrdenProduccion)
                .where(OrdenProduccion.id_orden == orden_id, OrdenProduccion.tenant_id == context.tenant_id)
            ).first()

            if orden_db:
                orden_db.sku_fk = sku
                orden_db.cantidad_esperada = cant
                orden_db.plan_fecha = fecha
                orden_db.origen = origen_auditoria
                orden_db.linea_id = linea_id  # Actualizamos la línea
                actualizadas += 1
            else:
                nueva_orden = OrdenProduccion(
                    tenant_id=context.tenant_id, id_orden=orden_id, sku_fk=sku,
                    cantidad_esperada=cant, plan_fecha=fecha, linea_id=linea_id,
                    origen=origen_auditoria
                )
                db.add(nueva_orden)
                creadas += 1

        except Exception as e:
            errores.append(f"Fila {index}: Error de formato ({str(e)})")

    db.commit()
    return {"status": "ok", "resultados": {"creadas": creadas, "actualizadas": actualizadas, "errores": errores}}

# ==========================================
# 2. IMPORTACIÓN DE CATÁLOGO (SKUs)
# ==========================================
@router.get("/skus/template", status_code=status.HTTP_200_OK)
def descargar_template_skus():
    df = pd.DataFrame(columns=["codigo_sku", "descripcion", "tiempo_ciclo_teorico", "unidades_por_ciclo"])
    df.loc[0] = ["SKU-123", "Pan Integral", "240", "4"]
    
    stream = io.StringIO()
    df.to_csv(stream, index=False)
    
    response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=template_catalogo_skus.csv"
    return response

@router.post("/skus/upload", status_code=status.HTTP_200_OK)
async def subir_skus(
    linea_id: str = Form(..., description="ID de la línea destino"),
    file: UploadFile = File(...),
    map_json: Optional[str] = Form(None), 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    verificar_permiso_carga_y_linea(db, context, linea_id)

    contenido = await file.read()
    try:
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python')
        elif file.filename.lower().endswith(('.xls', '.xlsx')):
            df = pd.read_excel(io.BytesIO(contenido))
        else:
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa CSV o Excel.")

    if map_json:
        try:
            mapeo = json.loads(map_json)
            df = df.rename(columns=mapeo)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="El map_json provisto no es un JSON válido.")

    if "codigo_sku" not in df.columns or "descripcion" not in df.columns:
        raise HTTPException(status_code=400, detail="Faltan columnas requeridas ('codigo_sku', 'descripcion').")

    creados, actualizados = 0, 0
    errores = []

    for index, row in df.iterrows():
        try:
            sku_code = str(row["codigo_sku"]).strip()
            if not sku_code or sku_code.lower() == "nan": 
                continue
                
            desc = str(row["descripcion"]).strip()
            
            t_ciclo = float(row["tiempo_ciclo_teorico"]) if "tiempo_ciclo_teorico" in df.columns and not pd.isna(row["tiempo_ciclo_teorico"]) else 240.0
            u_ciclo = int(row["unidades_por_ciclo"]) if "unidades_por_ciclo" in df.columns and not pd.isna(row["unidades_por_ciclo"]) else 1

            sku_db = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == sku_code, MaestroSKU.tenant_id == context.tenant_id)).first()

            if sku_db:
                sku_db.descripcion = desc
                sku_db.tiempo_ciclo_teorico = t_ciclo
                sku_db.unidades_por_ciclo = u_ciclo
                sku_db.linea_id = linea_id  # Actualizamos la línea
                actualizados += 1
            else:
                nuevo_sku = MaestroSKU(
                    tenant_id=context.tenant_id, 
                    codigo_sku=sku_code, 
                    descripcion=desc,
                    tiempo_ciclo_teorico=t_ciclo, 
                    unidades_por_ciclo=u_ciclo,
                    linea_id=linea_id
                )
                db.add(nuevo_sku)
                creados += 1

        except Exception as e:
            errores.append(f"Fila {index}: Error de formato ({str(e)})")

    db.commit()
    return {"status": "ok", "resultados": {"creados": creados, "actualizados": actualizados, "errores": errores}}
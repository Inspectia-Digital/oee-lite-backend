import logging

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
from app.core.clasificacion import validar_perfil_tiempos
from app.models.domain import OrdenProduccion, MaestroSKU, Tenant, Linea, PlanProduccion

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lite/importaciones", tags=["Importación y Exportación (UI)"])

# Fase K (auditoría QA #7): antes no había ningún límite de tamaño de
# archivo ni de filas -- un usuario autenticado podía agotar la memoria de
# una instancia Cloud Run de 512 MiB subiendo un archivo enorme. Límites
# deliberadamente generosos para no bloquear cargas reales de catálogo,
# pero acotados.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_FILAS_IMPORT = 20_000


async def _leer_archivo_acotado(file: UploadFile) -> bytes:
    """Lee como máximo MAX_UPLOAD_BYTES + 1 del archivo -- si lo excede,
    corta ahí en vez de bufferizar un archivo arbitrariamente grande
    completo en memoria antes de rechazarlo."""
    contenido = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contenido) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo supera el límite de {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB.",
        )
    return contenido


def _validar_cantidad_filas(df: pd.DataFrame) -> None:
    if len(df) > MAX_FILAS_IMPORT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El archivo tiene {len(df)} filas; el máximo soportado por carga es {MAX_FILAS_IMPORT}.",
        )

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
    # Unificación UX Planes/Órdenes/SKUs: antes esta importación creaba
    # órdenes SIN plan_id -- invisibles para "Plan del día"/avanzar-orden
    # (Fase AA), aunque la línea sí tuviera un Plan abierto. Ahora, desde
    # la vista de Planes en Configuración, la importación se dispara
    # siempre DENTRO de un plan elegido y lo pasa acá.
    plan_id: Optional[str] = Form(None, description="Opcional. Si se pasa, las órdenes cargadas/actualizadas quedan asociadas a este Plan."),
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    # 1. Validaciones Core (ERP & Planta)
    verificar_permiso_carga_y_linea(db, context, linea_id)

    plan_uuid: Optional[uuid.UUID] = None
    siguiente_secuencia = 1
    if plan_id:
        try:
            plan_uuid = uuid.UUID(plan_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="plan_id no es un UUID válido.")
        plan = db.exec(select(PlanProduccion).where(PlanProduccion.id == plan_uuid, PlanProduccion.tenant_id == context.tenant_id)).first()
        if not plan:
            raise HTTPException(status_code=400, detail="plan_id no existe o pertenece a otra organización.")
        if str(plan.linea_id) != str(linea_id):
            raise HTTPException(status_code=400, detail="plan_id pertenece a una línea distinta de linea_id.")
        maxima = db.exec(
            select(OrdenProduccion.secuencia)
            .where(OrdenProduccion.tenant_id == context.tenant_id, OrdenProduccion.plan_id == plan_uuid)
            .order_by(OrdenProduccion.secuencia.desc())
        ).first()
        siguiente_secuencia = (maxima or 0) + 1

    # 2. Leer archivo (acotado en tamaño, Fase K)
    contenido = await _leer_archivo_acotado(file)
    try:
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python')
        elif file.filename.lower().endswith(('.xls', '.xlsx')):
            df = pd.read_excel(io.BytesIO(contenido))
        else:
            raise ValueError()
    except HTTPException:
        raise
    except Exception as e:
        # No se refleja el texto crudo al cliente, pero antes tampoco
        # quedaba registrado del lado del servidor -- un error real (ej.
        # falta una dependencia para leer .xlsx) quedaba indistinguible
        # de "el usuario subió un archivo con formato raro".
        logger.warning(f"Fallo parseando archivo '{file.filename}': {e}")
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa CSV o Excel.")

    _validar_cantidad_filas(df)

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
                if plan_uuid:
                    # Reasigna al plan elegido; conserva la secuencia que ya
                    # tenía (re-subir el mismo id_orden no debería reordenar
                    # el plan, sólo actualizar sus datos).
                    orden_db.plan_id = plan_uuid
                actualizadas += 1
            else:
                nueva_orden = OrdenProduccion(
                    tenant_id=context.tenant_id, id_orden=orden_id, sku_fk=sku,
                    cantidad_esperada=cant, plan_fecha=fecha, linea_id=linea_id,
                    origen=origen_auditoria,
                    plan_id=plan_uuid,
                    secuencia=siguiente_secuencia if plan_uuid else 0,
                )
                db.add(nueva_orden)
                if plan_uuid:
                    siguiente_secuencia += 1
                creadas += 1

        except Exception as e:
            # Fase K (auditoría QA #8): no se refleja el texto crudo de la
            # excepción al cliente (puede incluir detalles internos de
            # pandas/tipos); se loguea completo del lado del servidor.
            logger.warning(f"Fila {index} del plan rechazada: {e}")
            errores.append(f"Fila {index}: no se pudo procesar (revisar tipos y formato de los datos).")

    db.commit()
    return {"status": "ok", "resultados": {"creadas": creadas, "actualizadas": actualizadas, "errores": errores}}

# ==========================================
# 2. IMPORTACIÓN DE CATÁLOGO (SKUs)
# ==========================================
@router.get("/skus/template", status_code=status.HTTP_200_OK)
def descargar_template_skus():
    # Fase AC: tiempo_lento_seg/tiempo_alerta_seg opcionales -- si se
    # omiten, el SKU clasifica con el piso de Línea hasta completarse.
    df = pd.DataFrame(columns=["codigo_sku", "descripcion", "tiempo_ideal_seg", "tiempo_lento_seg", "tiempo_alerta_seg", "unidades_por_ciclo"])
    df.loc[0] = ["SKU-123", "Pan Integral", "240", "280", "300", "4"]
    
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

    contenido = await _leer_archivo_acotado(file)
    try:
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python')
        elif file.filename.lower().endswith(('.xls', '.xlsx')):
            df = pd.read_excel(io.BytesIO(contenido))
        else:
            raise ValueError()
    except HTTPException:
        raise
    except Exception as e:
        # No se refleja el texto crudo al cliente, pero antes tampoco
        # quedaba registrado del lado del servidor -- un error real (ej.
        # falta una dependencia para leer .xlsx) quedaba indistinguible
        # de "el usuario subió un archivo con formato raro".
        logger.warning(f"Fallo parseando archivo '{file.filename}': {e}")
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa CSV o Excel.")

    _validar_cantidad_filas(df)

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

            # Fase AC: lento/alerta opcionales -- si faltan (no viene la
            # columna, o la celda está vacía), el SKU queda con perfil
            # incompleto y clasifica con el piso de Línea hasta
            # completarse (ver clasificacion.resolver_umbrales_evento).
            t_ideal = float(row["tiempo_ideal_seg"]) if "tiempo_ideal_seg" in df.columns and not pd.isna(row["tiempo_ideal_seg"]) else 240.0
            t_lento = float(row["tiempo_lento_seg"]) if "tiempo_lento_seg" in df.columns and not pd.isna(row["tiempo_lento_seg"]) else None
            t_alerta = float(row["tiempo_alerta_seg"]) if "tiempo_alerta_seg" in df.columns and not pd.isna(row["tiempo_alerta_seg"]) else None
            validar_perfil_tiempos(t_ideal, t_lento, t_alerta)
            u_ciclo = int(row["unidades_por_ciclo"]) if "unidades_por_ciclo" in df.columns and not pd.isna(row["unidades_por_ciclo"]) else 1

            sku_db = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == sku_code, MaestroSKU.tenant_id == context.tenant_id)).first()

            if sku_db:
                sku_db.descripcion = desc
                sku_db.tiempo_ideal_seg = t_ideal
                sku_db.tiempo_lento_seg = t_lento
                sku_db.tiempo_alerta_seg = t_alerta
                sku_db.unidades_por_ciclo = u_ciclo
                sku_db.linea_id = linea_id  # Actualizamos la línea
                actualizados += 1
            else:
                nuevo_sku = MaestroSKU(
                    tenant_id=context.tenant_id,
                    codigo_sku=sku_code,
                    descripcion=desc,
                    tiempo_ideal_seg=t_ideal,
                    tiempo_lento_seg=t_lento,
                    tiempo_alerta_seg=t_alerta,
                    unidades_por_ciclo=u_ciclo,
                    linea_id=linea_id
                )
                db.add(nuevo_sku)
                creados += 1

        except Exception as e:
            logger.warning(f"Fila {index} del catálogo de SKUs rechazada: {e}")
            errores.append(f"Fila {index}: no se pudo procesar (revisar tipos y formato de los datos).")

    db.commit()
    return {"status": "ok", "resultados": {"creados": creados, "actualizados": actualizados, "errores": errores}}
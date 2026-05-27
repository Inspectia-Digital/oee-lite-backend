from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile, File
from sqlmodel import Session, select
from app.core.database import get_session
from app.models.domain import Estacion, MotivoParada, Operario, Turno, MaestroSKU, OrdenProduccion, Linea, Supervisor, TipoParada
# 1. IMPORTAMOS LA NUEVA DEPENDENCIA MAGICA
from app.core.auth import obtener_tenant_aislado
from pydantic import BaseModel
from typing import Optional
from datetime import time
import uuid
import pandas as pd
import io

router = APIRouter(tags=["Configuracion y Maestros"])

# ==========================================
# --- MOLDES UPDATE (Para Edición Parcial) ---
# ==========================================
class EstacionUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[str] = None
    umbral_optimo: Optional[int] = None
    umbral_lento: Optional[int] = None
    umbral_alerta: Optional[int] = None
    activa: Optional[bool] = None
    posicion_linea: Optional[int] = None
    ramal: Optional[str] = None

class LineaUpdate(BaseModel):
    nombre: Optional[str] = None

class OperarioUpdate(BaseModel):
    legajo: Optional[str] = None
    nombre_completo: Optional[str] = None

class SupervisorUpdate(BaseModel):
    legajo: Optional[str] = None
    nombre_completo: Optional[str] = None

class TurnoUpdate(BaseModel):
    nombre: Optional[str] = None
    hora_inicio: Optional[time] = None
    hora_fin: Optional[time] = None
    linea_id: Optional[uuid.UUID] = None
    descanso_minutos: Optional[int] = None

class MotivoParadaUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo_parada: Optional[TipoParada] = None

# ==========================================
# ABM DE ESTACIONES
# ==========================================
@router.post("/estaciones/", response_model=Estacion)
def crear_estacion(
    estacion: Estacion, 
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    estacion.tenant_id = tenant_id
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion

@router.get("/estaciones/", response_model=list[Estacion])
def obtener_estaciones(
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    return db.exec(select(Estacion).where(Estacion.tenant_id == tenant_id)).all()

@router.patch("/estaciones/{estacion_id}", response_model=Estacion)
def actualizar_estacion(
    estacion_id: uuid.UUID = Path(...),
    datos_update: EstacionUpdate = None,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    estacion_db = db.get(Estacion, estacion_id)
    if not estacion_db or estacion_db.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Estación no encontrada")
    
    update_data = datos_update.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(estacion_db, key, value)
        
    db.add(estacion_db)
    db.commit()
    db.refresh(estacion_db)
    return estacion_db

# ==========================================
# ABM DE MOTIVOS DE PARADA
# ==========================================
@router.post("/motivos-parada/", response_model=MotivoParada)
def crear_motivo_parada(
    motivo: MotivoParada, 
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    motivo.tenant_id = tenant_id
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    return motivo

@router.get("/motivos-parada/", response_model=list[MotivoParada])
def obtener_motivos_parada(
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    return db.exec(select(MotivoParada).where(MotivoParada.tenant_id == tenant_id)).all()

@router.patch("/motivos-parada/{motivo_id}", response_model=MotivoParada)
def actualizar_motivo_parada(
    motivo_id: uuid.UUID = Path(...),
    datos_update: MotivoParadaUpdate = None,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    motivo_db = db.get(MotivoParada, motivo_id)
    if not motivo_db or motivo_db.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Motivo no encontrado")
    
    update_data = datos_update.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(motivo_db, key, value)
        
    db.add(motivo_db)
    db.commit()
    db.refresh(motivo_db)
    return motivo_db

# ==========================================
# ABM DE OPERARIOS
# ==========================================
@router.post("/operarios/", response_model=Operario)
def crear_operario(
    operario: Operario, 
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    operario.tenant_id = tenant_id
    db.add(operario)
    db.commit()
    db.refresh(operario)
    return operario

@router.get("/operarios/", response_model=list[Operario])
def obtener_operarios(
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    return db.exec(select(Operario).where(Operario.tenant_id == tenant_id)).all()

@router.patch("/operarios/{operario_id}", response_model=Operario)
def actualizar_operario(
    operario_id: uuid.UUID = Path(...),
    datos_update: OperarioUpdate = None,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    operario_db = db.get(Operario, operario_id)
    if not operario_db or operario_db.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Operario no encontrado")
    
    update_data = datos_update.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(operario_db, key, value)
        
    db.add(operario_db)
    db.commit()
    db.refresh(operario_db)
    return operario_db

# ==========================================
# ABM DE TURNOS
# ==========================================
@router.post("/turnos/", response_model=Turno)
def crear_turno(
    turno: Turno, 
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    turno.tenant_id = tenant_id
    db.add(turno)
    db.commit()
    db.refresh(turno)
    return turno

@router.get("/turnos/", response_model=list[Turno])
def obtener_turnos(
    linea_id: Optional[uuid.UUID] = None, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    query = select(Turno).where(Turno.tenant_id == tenant_id)
    if linea_id:
        query = query.where(Turno.linea_id == linea_id)
    return db.exec(query).all()

@router.patch("/turnos/{turno_id}", response_model=Turno)
def actualizar_turno(
    turno_id: uuid.UUID = Path(...),
    datos_update: TurnoUpdate = None,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    turno_db = db.get(Turno, turno_id)
    if not turno_db or turno_db.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Turno no encontrado")
    
    update_data = datos_update.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(turno_db, key, value)
        
    db.add(turno_db)
    db.commit()
    db.refresh(turno_db)
    return turno_db

# ==========================================
# ABM DE LÍNEAS
# ==========================================
@router.post("/lineas/", response_model=Linea)
def crear_linea(
    linea: Linea, 
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    linea.tenant_id = tenant_id
    db.add(linea)
    db.commit()
    db.refresh(linea)
    return linea

@router.get("/lineas/", response_model=list[Linea])
def obtener_lineas(
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    return db.exec(select(Linea).where(Linea.tenant_id == tenant_id)).all()

@router.patch("/lineas/{linea_id}", response_model=Linea)
def actualizar_linea(
    linea_id: uuid.UUID = Path(...),
    datos_update: LineaUpdate = None,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    linea_db = db.get(Linea, linea_id)
    if not linea_db or linea_db.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Línea no encontrada")
    
    update_data = datos_update.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(linea_db, key, value)
        
    db.add(linea_db)
    db.commit()
    db.refresh(linea_db)
    return linea_db

# ==========================================
# ABM DE SUPERVISORES
# ==========================================
@router.post("/supervisores/", response_model=Supervisor)
def crear_supervisor(
    supervisor: Supervisor, 
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    supervisor.tenant_id = tenant_id
    db.add(supervisor)
    db.commit()
    db.refresh(supervisor)
    return supervisor

@router.get("/supervisores/", response_model=list[Supervisor])
def obtener_supervisores(
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    return db.exec(select(Supervisor).where(Supervisor.tenant_id == tenant_id)).all()

@router.patch("/supervisores/{supervisor_id}", response_model=Supervisor)
def actualizar_supervisor(
    supervisor_id: uuid.UUID = Path(...),
    datos_update: SupervisorUpdate = None,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    supervisor_db = db.get(Supervisor, supervisor_id)
    if not supervisor_db or supervisor_db.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Supervisor no encontrado")
    
    update_data = datos_update.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(supervisor_db, key, value)
        
    db.add(supervisor_db)
    db.commit()
    db.refresh(supervisor_db)
    return supervisor_db

# ==========================================
# IMPORTADORES MASIVOS (FASE 2)
# ==========================================
@router.post("/upload/skus/")
def importar_maestro_skus(
    file: UploadFile = File(...), 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    contenido = file.file.read()
    try:
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python', encoding='utf-8-sig')
        else:
            df = pd.read_excel(io.BytesIO(contenido))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error de lectura: {str(e)}")

    df.columns = [str(c).replace('\ufeff', '').replace(';', '').strip().upper() for c in df.columns]
    
    if "SKU" not in df.columns or "DESCRIPCION" not in df.columns:
        raise HTTPException(status_code=400, detail=f"Faltan columnas obligatorias. Detectadas: {df.columns.tolist()}")

    creados, actualizados = 0, 0
    for _, row in df.iterrows():
        sku_code = str(row["SKU"]).strip()
        if not sku_code or sku_code.lower() == "nan": continue
        
        sku_db = db.exec(select(MaestroSKU).where(MaestroSKU.tenant_id == tenant_id, MaestroSKU.codigo_sku == sku_code)).first()
        
        if sku_db:
            sku_db.descripcion = str(row["DESCRIPCION"]).strip()
            actualizados += 1
        else:
            db.add(MaestroSKU(tenant_id=tenant_id, codigo_sku=sku_code, descripcion=str(row["DESCRIPCION"]).strip(), tiempo_ciclo_teorico=240.0))
            creados += 1
    
    db.commit()
    return {"status": "ok", "mensaje": f"Catálogo actualizado. Creados: {creados}, Actualizados: {actualizados}."}

@router.post("/upload/plan/")
def importar_plan_produccion(
    file: UploadFile = File(...), 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    contenido = file.file.read()
    try:
        df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python', header=None, encoding='utf-8-sig')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error en Plan: {str(e)}")

    lineas = 0
    for i in range(len(df)):
        try:
            fila = df.iloc[i]
            sku_id = str(fila[0]).strip()      
            cantidad = int(fila[3])            
            fecha_plan = str(fila[4]).strip()  
            
            nueva_op = OrdenProduccion(
                tenant_id=tenant_id,
                id_orden=f"OP-{sku_id[:5]}-{i}", 
                plan_fecha=fecha_plan,
                estado="abierta" 
            )
            db.add(nueva_op)
            lineas += 1
        except Exception as e:
            continue

    db.commit()
    return {"status": "ok", "mensaje": f"Plan cargado. {lineas} órdenes listas para fabricar."}

# ==========================================
# ENDPOINTS MANUALES Y UTILERÍA
# ==========================================
@router.post("/skus/", response_model=MaestroSKU)
def crear_sku_manual(
    sku: MaestroSKU, 
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    sku.tenant_id = tenant_id
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku

@router.post("/ordenes/", response_model=OrdenProduccion)
def crear_orden_manual(
    orden: OrdenProduccion, 
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    orden.tenant_id = tenant_id
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden

@router.post("/setup-springwall/")
def setup_springwall(
    db: Session = Depends(get_session), 
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    viejas = db.exec(select(Estacion).where(Estacion.tenant_id == tenant_id)).all()
    for v in viejas: db.delete(v)
    db.commit()

    e1 = Estacion(tenant_id=tenant_id, nombre="E1 - Pedalera (Ingreso)", tipo="sensor", posicion_linea=1, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    e2 = Estacion(tenant_id=tenant_id, nombre="E2 - Matelaceado", tipo="sensor", posicion_linea=2, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    e3 = Estacion(tenant_id=tenant_id, nombre="E3 - Forro/Escaneo", tipo="escaneo_manual", posicion_linea=3, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    db.add_all([e1, e2, e3])
    db.commit()

    cerradora_a_padre = Estacion(tenant_id=tenant_id, nombre="E4 - Cerradora A (Total)", tipo="escaneo_manual", posicion_linea=4, ramal="Línea A", umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    db.add(cerradora_a_padre)
    db.commit()
    db.refresh(cerradora_a_padre)

    sub_a1 = Estacion(tenant_id=tenant_id, nombre="E4.1 - Cerradora A (Etapa 1)", tipo="escaneo_manual", parent_id=cerradora_a_padre.id, posicion_linea=4, ramal="Línea A", umbral_optimo=120, umbral_lento=140, umbral_alerta=150)
    sub_a2 = Estacion(tenant_id=tenant_id, nombre="E4.2 - Cerradora A (Etapa 2)", tipo="escaneo_manual", parent_id=cerradora_a_padre.id, posicion_linea=4, ramal="Línea A", umbral_optimo=120, umbral_lento=140, umbral_alerta=150)
    db.add_all([sub_a1, sub_a2])

    cerradora_b = Estacion(tenant_id=tenant_id, nombre="E5 - Cerradora B", tipo="escaneo_manual", posicion_linea=4, ramal="Línea B", umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    calidad_a = Estacion(tenant_id=tenant_id, nombre="E6 - Calidad A", tipo="calidad", posicion_linea=5, ramal="Línea A", umbral_optimo=120, umbral_lento=180, umbral_alerta=181)
    calidad_b = Estacion(tenant_id=tenant_id, nombre="E7 - Calidad B", tipo="calidad", posicion_linea=5, ramal="Línea B", umbral_optimo=120, umbral_lento=180, umbral_alerta=181)
    
    db.add_all([cerradora_b, calidad_a, calidad_b])
    db.commit()

    return {"status": "ok", "mensaje": "Línea Springwall cargada de forma segura."}

@router.delete("/reset-db-danger/")
def reset_base_de_datos():
    from app.core.database import engine
    from sqlmodel import SQLModel
    try:
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        return {"status": "ok", "mensaje": "Base de datos reseteada."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al resetear: {str(e)}")
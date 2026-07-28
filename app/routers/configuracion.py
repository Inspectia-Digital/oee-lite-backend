from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile, File, status
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import List, Optional
from datetime import time
import uuid
import pandas as pd
import io
from sqlalchemy.exc import IntegrityError

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant, TenantContext, get_usuario_actual
from app.models.domain import (
    Estacion, MotivoParada, Operario, Turno, MaestroSKU, OrdenProduccion, 
    Linea, Supervisor, TipoParada, RolUsuario, Planta, ModoAsignacionOperarios, ModoAsignacionOperariosEstacion
)

router = APIRouter(prefix="/config", tags=["Configuración y Maestros"])

# ==========================================
# 🛡️ MIDDLEWARE LOCAL (RBAC)
# ==========================================
def requerir_gerencia(usuario: UsuarioSaaS = Depends(get_usuario_actual)):
    """Asegura que solo Gerencia o SuperAdmin puedan modificar la configuración de la planta."""
    if usuario.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA]:
        raise HTTPException(status_code=403, detail="Acceso denegado. Requiere privilegios de Gerencia.")
    return usuario

# ==========================================
# 📦 SCHEMAS SEGUROS (Evitan inyección de IDs)
# ==========================================
class LineaCreate(BaseModel):
    nombre: str
    planta_id: uuid.UUID
    modo_asignacion_operarios: Optional[ModoAsignacionOperarios] = ModoAsignacionOperarios.MANUAL

class EstacionCreate(BaseModel):
    nombre: str
    tipo: str
    linea_id: Optional[uuid.UUID] = None
    parent_id: Optional[uuid.UUID] = None
    posicion_linea: int = 1
    ramal: str = "Principal"
    umbral_optimo: int = 240
    umbral_lento: int = 280
    umbral_alerta: int = 300
    codigo_plc: Optional[str] = None
    modo_asignacion_operarios: Optional[ModoAsignacionOperariosEstacion] = ModoAsignacionOperariosEstacion.HEREDAR

class EstacionUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[str] = None
    umbral_optimo: Optional[int] = None
    umbral_lento: Optional[int] = None
    umbral_alerta: Optional[int] = None
    activa: Optional[bool] = None
    posicion_linea: Optional[int] = None
    ramal: Optional[str] = None
    codigo_plc: Optional[str] = None

class MotivoParadaCreate(BaseModel):
    nombre: str
    tipo_parada: TipoParada

class TurnoCreate(BaseModel):
    nombre: str
    hora_inicio: time
    hora_fin: time
    descanso_minutos: int = 0
    linea_id: Optional[uuid.UUID] = None


# ==========================================
# 🏭 ABM DE LÍNEAS (Con Validación Cross-Tenant)
# ==========================================
@router.post("/lineas/", response_model=Linea, status_code=status.HTTP_201_CREATED)
def crear_linea(
    payload: LineaCreate, 
    db: Session = Depends(get_session), 
    context: TenantContext = Depends(obtener_contexto_tenant),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    # Cross-Tenant Validation: Evita asignar la línea a una planta de otra empresa
    planta_db = db.exec(select(Planta).where(Planta.id == payload.planta_id, Planta.tenant_id == context.tenant_id)).first()
    if not planta_db:
        raise HTTPException(status_code=400, detail="La planta no existe o pertenece a otra organización.")

    nueva_linea = Linea(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nueva_linea)
    db.commit()
    db.refresh(nueva_linea)
    return nueva_linea

@router.get("/lineas/", response_model=List[Linea])
def obtener_lineas(db: Session = Depends(get_session), context: TenantContext = Depends(obtener_contexto_tenant)):
    return db.exec(select(Linea).where(Linea.tenant_id == context.tenant_id)).all()


# ==========================================
# ⚙️ ABM DE ESTACIONES (Maquinas)
# ==========================================
@router.post("/estaciones/", response_model=Estacion, status_code=status.HTTP_201_CREATED)
def crear_estacion(
    payload: EstacionCreate, 
    db: Session = Depends(get_session), 
    context: TenantContext = Depends(obtener_contexto_tenant),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    if payload.linea_id:
        linea_db = db.exec(select(Linea).where(Linea.id == payload.linea_id, Linea.tenant_id == context.tenant_id)).first()
        if not linea_db: raise HTTPException(status_code=400, detail="Línea inválida.")

    nueva_estacion = Estacion(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nueva_estacion)
    db.commit()
    db.refresh(nueva_estacion)
    return nueva_estacion

@router.get("/estaciones/", response_model=List[Estacion])
def obtener_estaciones(db: Session = Depends(get_session), context: TenantContext = Depends(obtener_contexto_tenant)):
    return db.exec(select(Estacion).where(Estacion.tenant_id == context.tenant_id)).all()

@router.patch("/estaciones/{estacion_id}", response_model=Estacion)
def actualizar_estacion(
    estacion_id: uuid.UUID,
    payload: EstacionUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    estacion_db = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion_db: raise HTTPException(status_code=404, detail="Estación no encontrada")
    
    update_data = payload.model_dump(exclude_unset=True) 
    for key, value in update_data.items(): setattr(estacion_db, key, value)
        
    db.add(estacion_db)
    db.commit()
    db.refresh(estacion_db)
    return estacion_db

@router.delete("/estaciones/{estacion_id}")
def eliminar_estacion(
    estacion_id: uuid.UUID, 
    db: Session = Depends(get_session), 
    context: TenantContext = Depends(obtener_contexto_tenant),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    estacion_db = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion_db: raise HTTPException(status_code=404, detail="Estación no encontrada")
    try:
        db.delete(estacion_db)
        db.commit()
        return {"mensaje": "Estación eliminada"}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="No se puede eliminar la estación porque tiene escaneos o paradas asociadas.")


# ==========================================
# 🛑 ABM DE MOTIVOS DE PARADA
# ==========================================
@router.post("/motivos-parada/", response_model=MotivoParada, status_code=status.HTTP_201_CREATED)
def crear_motivo_parada(
    payload: MotivoParadaCreate, 
    db: Session = Depends(get_session), 
    context: TenantContext = Depends(obtener_contexto_tenant),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    nuevo_motivo = MotivoParada(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nuevo_motivo)
    db.commit()
    db.refresh(nuevo_motivo)
    return nuevo_motivo

@router.get("/motivos-parada/", response_model=List[MotivoParada])
def obtener_motivos_parada(db: Session = Depends(get_session), context: TenantContext = Depends(obtener_contexto_tenant)):
    return db.exec(select(MotivoParada).where(MotivoParada.tenant_id == context.tenant_id)).all()


# ==========================================
# ⏱️ ABM DE TURNOS
# ==========================================
@router.post("/turnos/", response_model=Turno, status_code=status.HTTP_201_CREATED)
def crear_turno(
    payload: TurnoCreate, 
    db: Session = Depends(get_session), 
    context: TenantContext = Depends(obtener_contexto_tenant),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    nuevo_turno = Turno(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nuevo_turno)
    db.commit()
    db.refresh(nuevo_turno)
    return nuevo_turno

@router.get("/turnos/", response_model=List[Turno])
def obtener_turnos(
    linea_id: Optional[uuid.UUID] = None, 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    query = select(Turno).where(Turno.tenant_id == context.tenant_id)
    if linea_id: query = query.where(Turno.linea_id == linea_id)
    return db.exec(query).all()


# ==========================================
# 📊 ERP INTEGRATION: IMPORTACIÓN MASIVA (CERO FRICCIÓN)
# ==========================================
@router.post("/erp/skus/bulk", tags=["Integración ERP"])
async def importar_skus_csv(
    file: UploadFile = File(...), 
    db: Session = Depends(get_session), 
    context: TenantContext = Depends(obtener_contexto_tenant),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    """Importa o actualiza el Maestro de SKUs desde un CSV (codigo_sku, descripcion, tiempo_ciclo_teorico)."""
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Debe ser un archivo CSV")
    
    contenido = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(contenido))
        requeridos = {'codigo_sku', 'descripcion', 'tiempo_ciclo_teorico'}
        if not requeridos.issubset(df.columns):
            raise ValueError(f"Faltan columnas requeridas: {requeridos - set(df.columns)}")
            
        skus_procesados = 0
        for _, row in df.iterrows():
            sku_db = db.exec(select(MaestroSKU).where(
                MaestroSKU.codigo_sku == str(row['codigo_sku']), 
                MaestroSKU.tenant_id == context.tenant_id
            )).first()
            
            if sku_db:
                sku_db.descripcion = str(row['descripcion'])
                sku_db.tiempo_ciclo_teorico = float(row['tiempo_ciclo_teorico'])
            else:
                nuevo_sku = MaestroSKU(
                    tenant_id=context.tenant_id,
                    codigo_sku=str(row['codigo_sku']),
                    descripcion=str(row['descripcion']),
                    tiempo_ciclo_teorico=float(row['tiempo_ciclo_teorico'])
                )
                db.add(nuevo_sku)
            skus_procesados += 1
            
        db.commit()
        return {"mensaje": f"Se procesaron {skus_procesados} SKUs correctamente."}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Error procesando CSV: {str(e)}")


@router.get("/erp/skus", response_model=List[MaestroSKU], tags=["Integración ERP"])
def listar_skus(db: Session = Depends(get_session), context: TenantContext = Depends(obtener_contexto_tenant)):
    return db.exec(select(MaestroSKU).where(MaestroSKU.tenant_id == context.tenant_id)).all()
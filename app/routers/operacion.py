from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
import uuid
from pydantic import BaseModel
from datetime import datetime

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext
from app.models.domain import (
    ParadaDetectada, MotivoParada, EstadoParada, 
    Estacion, Linea, LiteEventoProduccion, Operario
)

router = APIRouter(prefix="/supervisor", tags=["Operacion (UI Supervisor)"])

class ClasificarParada(BaseModel):
    motivo_fk: uuid.UUID

class ParadaPlanificadaCreate(BaseModel):
    estacion_fk: uuid.UUID
    motivo_fk: uuid.UUID
    inicio: datetime
    fin: datetime

class AsignacionRetroactiva(BaseModel):
    estacion_fk: uuid.UUID
    operario_fk: uuid.UUID
    inicio: datetime
    fin: datetime

def validar_planta(context: TenantContext):
    """Asegura que el supervisor seleccionó una planta en el OS Shell."""
    if not context.sub_tenant_id:
        raise HTTPException(status_code=400, detail="Falta Header X-Sub-Tenant-Id. Seleccione una Planta.")

@router.get("/paradas-pendientes", response_model=list[ParadaDetectada])
def obtener_paradas_pendientes(
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Obtiene paradas huérfanas filtradas estrictamente por la Planta activa[cite: 13]."""
    validar_planta(context)

    query = (
        select(ParadaDetectada)
        .join(Estacion)
        .join(Linea)
        .where(
            ParadaDetectada.tenant_id == context.tenant_id,
            ParadaDetectada.estado == EstadoParada.PENDIENTE,
            Linea.planta_id == context.sub_tenant_id # 🔒 Aislamiento de Planta
        )
    )
    return db.exec(query).all()

@router.patch("/paradas/{parada_id}/clasificar", response_model=ParadaDetectada)
def clasificar_parada(
    parada_id: uuid.UUID, 
    datos: ClasificarParada, 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    parada = db.get(ParadaDetectada, parada_id)
    if not parada or parada.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Parada no encontrada en su empresa[cite: 13]")
    
    motivo = db.get(MotivoParada, datos.motivo_fk)
    if not motivo or motivo.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Motivo de parada no válido o no autorizado[cite: 13]")

    parada.motivo_fk = motivo.id
    parada.estado = EstadoParada.CLASIFICADA 
    
    db.add(parada)
    db.commit()
    db.refresh(parada)
    return parada

@router.post("/paradas/planificadas", response_model=ParadaDetectada)
def registrar_parada_planificada(
    datos: ParadaPlanificadaCreate, 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    estacion = db.get(Estacion, datos.estacion_fk)
    if not estacion or estacion.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Estación no encontrada")

    motivo = db.get(MotivoParada, datos.motivo_fk)
    if not motivo or motivo.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Motivo no encontrado")
        
    if "planificada" not in str(motivo.tipo_parada).lower():
        raise HTTPException(status_code=400, detail="El motivo seleccionado no es PLANIFICADA[cite: 13]")
    
    duracion = (datos.fin - datos.inicio).total_seconds()
    if duracion <= 0:
         raise HTTPException(status_code=400, detail="La fecha de fin debe ser mayor a la de inicio[cite: 13]")

    nueva_parada = ParadaDetectada(
        tenant_id=context.tenant_id,
        estacion_fk=datos.estacion_fk,
        motivo_fk=motivo.id,
        inicio=datos.inicio,
        fin=datos.fin,
        duracion_segundos=duracion,
        estado=EstadoParada.CLASIFICADA
    )
    db.add(nueva_parada)
    db.commit()
    db.refresh(nueva_parada)
    return nueva_parada

@router.post("/operarios/asignar-retroactivo")
def asignar_operario_retroactivo(
    datos: AsignacionRetroactiva, 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """
    (Fallback) Si el operario olvidó escanear su legajo, el supervisor le asigna 
    los eventos producidos en una ventana de tiempo[cite: 13].
    """
    operario = db.get(Operario, datos.operario_fk)
    if not operario or operario.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Operario no encontrado en su empresa[cite: 13]")

    # Se unifica a la nueva tabla base: LiteEventoProduccion
    eventos = db.exec(
        select(LiteEventoProduccion).where(
            LiteEventoProduccion.tenant_id == context.tenant_id,
            LiteEventoProduccion.id_estacion == str(datos.estacion_fk),
            LiteEventoProduccion.timestamp >= datos.inicio,
            LiteEventoProduccion.timestamp <= datos.fin
        )
    ).all()

    if not eventos:
        return {"mensaje": "No se encontraron eventos en ese rango.", "actualizados": 0}

    # CTO Note: Requiere que agregues el campo operario_fk en LiteEventoProduccion en domain.py si aún no lo tiene.
    for evento in eventos:
        if hasattr(evento, "operario_fk"):
            setattr(evento, "operario_fk", operario.id) 
            db.add(evento)

    db.commit()
    return {
        "mensaje": f"Se asignaron {len(eventos)} escaneos a {operario.nombre_completo}[cite: 13]", 
        "actualizados": len(eventos)
    }
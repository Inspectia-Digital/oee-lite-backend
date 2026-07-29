from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
import uuid
from pydantic import BaseModel
from datetime import datetime

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext, get_usuario_actual
from app.models.domain import (
    ParadaDetectada, MotivoParada, EstadoParada,
    Estacion, Linea, LiteEventoProduccion, Operario, UsuarioSaaS, RolUsuario, UsuarioPlanta
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

def validar_planta(context: TenantContext, usuario: UsuarioSaaS, db: Session):
    """RBAC geolocalizado (Fase D.3): Gerencia/SuperAdmin acceden a todo el
    tenant. Supervisor/Operario deben tener una planta seleccionada Y estar
    realmente asignados a ella vía UsuarioPlanta (antes sólo se chequeaba
    que el header existiera, sin validar la asignación real)."""
    if not context.sub_tenant_id:
        raise HTTPException(status_code=400, detail="Falta Header X-Sub-Tenant-Id. Seleccione una Planta.")

    if usuario.rol in (RolUsuario.SUPERADMIN, RolUsuario.GERENCIA):
        return

    asignacion = db.exec(
        select(UsuarioPlanta).where(
            UsuarioPlanta.usuario_id == usuario.id,
            UsuarioPlanta.planta_id == uuid.UUID(context.sub_tenant_id),
            UsuarioPlanta.activo == True,  # noqa: E712
        )
    ).first()
    if not asignacion:
        raise HTTPException(status_code=403, detail="No tiene acceso a esta planta.")

@router.get("/paradas-pendientes", response_model=list[ParadaDetectada])
def obtener_paradas_pendientes(
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    """Obtiene paradas huérfanas filtradas estrictamente por la Planta activa[cite: 13]."""
    validar_planta(context, usuario, db)

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
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)
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
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)
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
def asignar_operario_retroactivo():
    """
    DESHABILITADO (Fase D.3, ver HANDOFF_STG_PRODUCTION_GRADE.md, sección
    "Asignación de operarios"): este endpoint pretendía actualizar
    LiteEventoProduccion.operario_fk, campo que nunca existió en el modelo
    (el código hacía hasattr(evento, "operario_fk"), siempre False, así que
    devolvía "éxito" sin persistir nada — hallazgo STP-007 de la auditoría).

    El HANDOFF prohíbe explícitamente actualizar eventos históricos para
    asignar operario. El reemplazo (Fase D.4b) resuelve el operario en
    tiempo de lectura vía AsignacionTurno (tenant + estación + turno +
    fecha), sin tocar eventos ya persistidos.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Endpoint deshabilitado: nunca persistía la asignación (bug STP-007). "
        "La asignación de operarios se resuelve en tiempo de lectura vía AsignacionTurno (Fase D.4b).",
    )
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
import uuid
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, date, timedelta

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext, get_usuario_actual
from app.models.domain import (
    ParadaDetectada, MotivoParada, EstadoParada,
    Estacion, Linea, LiteEventoProduccion, Operario, UsuarioSaaS, RolUsuario, UsuarioPlanta,
    AsignacionTurno, AsignacionSupervisor, Turno, Supervisor,
)

router = APIRouter(prefix="/supervisor", tags=["Operacion (UI Supervisor)"])

# Router aparte (sin prefijo /supervisor) para el tablero de asignación de
# supervisores, que BACKEND_REQUIREMENTS.md SS11.2 documenta bajo /asignaciones/supervisor/.
router_asignaciones_supervisor = APIRouter(prefix="/asignaciones", tags=["Operacion (UI Supervisor)"])

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


def _validar_linea_en_planta(linea_id: uuid.UUID, context: TenantContext, db: Session) -> Linea:
    linea = db.exec(select(Linea).where(Linea.id == linea_id, Linea.tenant_id == context.tenant_id)).first()
    if not linea:
        raise HTTPException(status_code=404, detail="Línea no encontrada.")
    if context.sub_tenant_id and str(linea.planta_id) != str(context.sub_tenant_id):
        raise HTTPException(status_code=404, detail="Línea no encontrada en la planta activa.")
    return linea


# ==========================================
# TABLERO DE DOTACIÓN: ASIGNACIÓN OPERARIO ↔ ESTACIÓN (Fase H)
# BACKEND_REQUIREMENTS.md §14 ("supervisor.asignaciones") / §11.4.
# Idempotente por (fecha, turno_fk, estacion_fk): reasignar sobrescribe al
# operario anterior. DELETE libera la estación (hard-delete real: es una
# fila de agenda diaria, no un maestro con historial que preservar).
# ==========================================
class AsignacionTurnoCreate(BaseModel):
    fecha: date
    estacion_fk: uuid.UUID
    operario_fk: uuid.UUID
    turno_fk: uuid.UUID


@router.get("/asignaciones/", response_model=List[AsignacionTurno])
def listar_asignaciones_dotacion(
    fecha: date,
    linea_id: uuid.UUID,
    turno_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)
    _validar_linea_en_planta(linea_id, context, db)

    query = (
        select(AsignacionTurno)
        .join(Estacion, AsignacionTurno.estacion_fk == Estacion.id)
        .where(
            AsignacionTurno.tenant_id == context.tenant_id,
            AsignacionTurno.fecha == fecha,
            Estacion.linea_id == linea_id,
        )
    )
    if turno_id:
        query = query.where(AsignacionTurno.turno_fk == turno_id)
    return db.exec(query).all()


@router.post("/asignaciones/", response_model=AsignacionTurno, status_code=status.HTTP_201_CREATED)
def asignar_dotacion(
    payload: AsignacionTurnoCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)

    estacion = db.exec(select(Estacion).where(Estacion.id == payload.estacion_fk, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada.")
    if context.sub_tenant_id:
        _validar_linea_en_planta(estacion.linea_id, context, db)

    operario = db.exec(select(Operario).where(Operario.id == payload.operario_fk, Operario.tenant_id == context.tenant_id, Operario.activo == True)).first()  # noqa: E712
    if not operario:
        raise HTTPException(status_code=404, detail="Operario no encontrado o inactivo.")

    turno = db.exec(select(Turno).where(Turno.id == payload.turno_fk, Turno.tenant_id == context.tenant_id)).first()
    if not turno:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")

    existente = db.exec(
        select(AsignacionTurno).where(
            AsignacionTurno.tenant_id == context.tenant_id,
            AsignacionTurno.fecha == payload.fecha,
            AsignacionTurno.turno_fk == payload.turno_fk,
            AsignacionTurno.estacion_fk == payload.estacion_fk,
        )
    ).first()
    if existente:
        existente.operario_fk = payload.operario_fk
        db.add(existente)
        db.commit()
        db.refresh(existente)
        return existente

    nueva = AsignacionTurno(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva


@router.delete("/asignaciones/{asignacion_id}")
def liberar_estacion(
    asignacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)
    asignacion = db.exec(
        select(AsignacionTurno).where(AsignacionTurno.id == asignacion_id, AsignacionTurno.tenant_id == context.tenant_id)
    ).first()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")
    db.delete(asignacion)
    db.commit()
    return {"mensaje": "Estación liberada."}


# ==========================================
# MONITOR DE EVENTOS EN VIVO (Fase H)
# BACKEND_REQUIREMENTS.md §14 ("eventos.live").
# ==========================================
class EventoLive(BaseModel):
    estacion_id: uuid.UUID
    operario_id: Optional[uuid.UUID] = None
    timestamp: datetime
    estado: str


@router.get("/eventos/live", response_model=List[EventoLive])
def eventos_en_vivo(
    limite: int = 100,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    """Últimos eventos de escaneo para el monitor del supervisor, más
    recientes primero. La resolución de operario_id es best-effort: se
    busca la AsignacionTurno de esa estación para la fecha del evento (sin
    matchear turno exacto, LiteEventoProduccion no guarda a qué turno
    pertenece cada evento) -- suficiente para un monitor en vivo, no para
    reportes de precisión."""
    validar_planta(context, usuario, db)

    estaciones_planta = db.exec(
        select(Estacion.id)
        .join(Linea, Estacion.linea_id == Linea.id)
        .where(
            Estacion.tenant_id == context.tenant_id,
            Linea.planta_id == context.sub_tenant_id,
        )
    ).all()
    ids_estacion_str = {str(eid) for eid in estaciones_planta}
    if not ids_estacion_str:
        return []

    eventos = db.exec(
        select(LiteEventoProduccion)
        .where(
            LiteEventoProduccion.tenant_id == context.tenant_id,
            LiteEventoProduccion.id_estacion.in_(ids_estacion_str),
        )
        .order_by(LiteEventoProduccion.timestamp.desc())
        .limit(limite)
    ).all()
    if not eventos:
        return []

    fechas = {e.timestamp.date() for e in eventos}
    asignaciones = db.exec(
        select(AsignacionTurno).where(
            AsignacionTurno.tenant_id == context.tenant_id,
            AsignacionTurno.estacion_fk.in_([uuid.UUID(i) for i in ids_estacion_str]),
            AsignacionTurno.fecha.in_(fechas),
        )
    ).all()
    operario_por_estacion_fecha = {(str(a.estacion_fk), a.fecha): a.operario_fk for a in asignaciones}

    resultado = []
    for e in eventos:
        operario_id = operario_por_estacion_fecha.get((e.id_estacion, e.timestamp.date()))
        resultado.append(EventoLive(
            estacion_id=uuid.UUID(e.id_estacion),
            operario_id=operario_id,
            timestamp=e.timestamp,
            estado=e.estado,
        ))
    return resultado


# ==========================================
# ASIGNACIÓN DE SUPERVISORES POR DÍA (Fase H)
# BACKEND_REQUIREMENTS.md §11.2. Path a nivel raíz (no bajo /supervisor)
# porque así lo especifica la doc. Idempotente por (fecha, linea_id,
# turno_id): reasignar sobrescribe.
# ==========================================
class AsignacionSupervisorCreate(BaseModel):
    fecha: date
    linea_id: uuid.UUID
    turno_id: uuid.UUID
    supervisor_id: uuid.UUID


@router_asignaciones_supervisor.get("/supervisor/", response_model=List[AsignacionSupervisor])
def listar_asignaciones_supervisor(
    fecha: date,
    linea_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)
    query = select(AsignacionSupervisor).where(
        AsignacionSupervisor.tenant_id == context.tenant_id,
        AsignacionSupervisor.fecha == fecha,
    )
    if linea_id:
        _validar_linea_en_planta(linea_id, context, db)
        query = query.where(AsignacionSupervisor.linea_id == linea_id)
    return db.exec(query).all()


@router_asignaciones_supervisor.post("/supervisor/", response_model=AsignacionSupervisor, status_code=status.HTTP_201_CREATED)
def asignar_supervisor(
    payload: AsignacionSupervisorCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)
    _validar_linea_en_planta(payload.linea_id, context, db)

    turno = db.exec(select(Turno).where(Turno.id == payload.turno_id, Turno.tenant_id == context.tenant_id)).first()
    if not turno:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")

    supervisor = db.exec(
        select(Supervisor).where(Supervisor.id == payload.supervisor_id, Supervisor.tenant_id == context.tenant_id, Supervisor.activo == True)  # noqa: E712
    ).first()
    if not supervisor:
        raise HTTPException(status_code=404, detail="Supervisor no encontrado o inactivo.")

    existente = db.exec(
        select(AsignacionSupervisor).where(
            AsignacionSupervisor.tenant_id == context.tenant_id,
            AsignacionSupervisor.fecha == payload.fecha,
            AsignacionSupervisor.linea_id == payload.linea_id,
            AsignacionSupervisor.turno_id == payload.turno_id,
        )
    ).first()
    if existente:
        existente.supervisor_id = payload.supervisor_id
        db.add(existente)
        db.commit()
        db.refresh(existente)
        return existente

    nueva = AsignacionSupervisor(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva
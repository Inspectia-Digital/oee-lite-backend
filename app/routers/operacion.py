from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from sqlalchemy import func
import uuid
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, date, time, timedelta

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext, get_usuario_actual
from app.models.domain import (
    ParadaDetectada, MotivoParada, EstadoParada,
    Estacion, Linea, LiteEventoProduccion, Operario, UsuarioSaaS, RolUsuario, UsuarioPlanta,
    AsignacionTurno, AsignacionSupervisor, Turno, Supervisor,
    PlanProduccion, OrdenProduccion, EstadoPlan, EstadoOrden,
)

router = APIRouter(prefix="/supervisor", tags=["Operacion (UI Supervisor)"])

# Router aparte (sin prefijo /supervisor) para el tablero de asignación de
# supervisores, que BACKEND_REQUIREMENTS.md SS11.2 documenta bajo /asignaciones/supervisor/.
router_asignaciones_supervisor = APIRouter(prefix="/asignaciones", tags=["Operacion (UI Supervisor)"])

# Rol "Encargado" (pedido explícito del usuario): intermedio entre
# Supervisor y Operario -- tiene cuenta web, pero sólo puede ver/clasificar
# paradas (obtener_paradas_pendientes, clasificar_parada,
# listar_historial_paradas -- sin chequeo de rol, alcanza con planta
# asignada, igual que siempre). Todo lo demás en este archivo (dotación,
# asignación de supervisores, avanzar Plan, paradas planificadas) usa esta
# constante para excluirlo explícitamente -- antes esos endpoints no
# chequeaban rol en absoluto, sólo pertenencia a la planta (validar_planta),
# así que agregar el rol nuevo sin este guard le habría dado acceso a todo.
ROLES_SUPERVISION_COMPLETA = (RolUsuario.SUPERVISOR, RolUsuario.GERENCIA, RolUsuario.PRODUCCION, RolUsuario.SUPERADMIN)

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

class ParadaHistorialRow(BaseModel):
    id: uuid.UUID
    estacion_fk: uuid.UUID
    estacion_nombre: str
    linea_id: uuid.UUID
    linea_nombre: str
    inicio: datetime
    fin: Optional[datetime] = None
    duracion_segundos: Optional[float] = None
    estado: EstadoParada
    origen: str
    motivo_fk: Optional[uuid.UUID] = None
    motivo_nombre: Optional[str] = None

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
    # QA-06 (auditoría QA): validar_planta() ya confirmó que el usuario
    # tiene acceso a la planta activa -- esto confirma que la PARADA
    # (vía su estación) pertenece a esa misma planta, no sólo al tenant.
    _validar_estacion_en_planta(parada.estacion_fk, context, db)

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
    # Programar una parada FUTURA es distinto de clasificar una que ya
    # ocurrió -- confirmado con el usuario: Encargado queda afuera de esto.
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para programar paradas planificadas.")
    estacion = db.get(Estacion, datos.estacion_fk)
    if not estacion or estacion.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Estación no encontrada")
    # QA-06 (auditoría QA): la estación debe pertenecer a la planta
    # activa, no sólo al tenant.
    if estacion.linea_id:
        _validar_linea_en_planta(estacion.linea_id, context, db)

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
        estado=EstadoParada.CLASIFICADA,
        origen="PLANIFICADA",
    )
    db.add(nueva_parada)
    db.commit()
    db.refresh(nueva_parada)
    return nueva_parada


# ==========================================
# HISTORIAL DE PARADAS (Fase N -- auditoría de producción #8/#9)
# Antes sólo existía /paradas-pendientes (estado=PENDIENTE); ni el
# historial de clasificadas ni el listado de programadas tenían un GET
# real -- el front las leía de un mock local en memoria.
# ==========================================
@router.get("/paradas", response_model=List[ParadaHistorialRow])
def listar_historial_paradas(
    estado: Optional[EstadoParada] = None,
    origen: Optional[str] = None,
    linea_id: Optional[uuid.UUID] = None,
    motivo_fk: Optional[uuid.UUID] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    """Historial completo de paradas, filtrable. `origen=PLANIFICADA` da el
    listado de "programadas"; `estado=clasificada&origen=AUTOMATICA` da el
    historial de clasificadas por el supervisor. Sin filtros, trae todo
    (paginado). Misma planta activa que el resto de /supervisor."""
    validar_planta(context, usuario, db)
    if not (1 <= limit <= 500):
        raise HTTPException(status_code=400, detail="limit debe estar entre 1 y 500.")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset no puede ser negativo.")

    query = (
        select(ParadaDetectada, Estacion, Linea, MotivoParada)
        .join(Estacion, ParadaDetectada.estacion_fk == Estacion.id)
        .join(Linea, Estacion.linea_id == Linea.id)
        .outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id)
        .where(
            ParadaDetectada.tenant_id == context.tenant_id,
            Linea.planta_id == context.sub_tenant_id,
        )
    )
    if estado is not None:
        query = query.where(ParadaDetectada.estado == estado)
    if origen is not None:
        query = query.where(ParadaDetectada.origen == origen)
    if linea_id is not None:
        query = query.where(Linea.id == linea_id)
    if motivo_fk is not None:
        query = query.where(ParadaDetectada.motivo_fk == motivo_fk)
    if fecha_desde is not None:
        query = query.where(ParadaDetectada.inicio >= datetime.combine(fecha_desde, time.min))
    if fecha_hasta is not None:
        query = query.where(ParadaDetectada.inicio <= datetime.combine(fecha_hasta, time.max))

    query = query.order_by(ParadaDetectada.inicio.desc()).offset(offset).limit(limit)
    filas = db.exec(query).all()

    return [
        ParadaHistorialRow(
            id=p.id, estacion_fk=p.estacion_fk, estacion_nombre=e.nombre,
            linea_id=l.id, linea_nombre=l.nombre,
            inicio=p.inicio, fin=p.fin, duracion_segundos=p.duracion_segundos,
            estado=p.estado, origen=p.origen,
            motivo_fk=p.motivo_fk, motivo_nombre=m.nombre if m else None,
        )
        for p, e, l, m in filas
    ]


@router.delete("/paradas/{parada_id}")
def eliminar_parada_planificada(
    parada_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    """Deshace una parada programada por error, mientras no haya empezado
    todavía. No se puede borrar una parada AUTOMATICA ni una PLANIFICADA
    que ya está en curso o pasada -- ahí se preserva trazabilidad real de
    downtime, no es una fila de agenda editable."""
    validar_planta(context, usuario, db)
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para eliminar paradas planificadas.")
    parada = db.get(ParadaDetectada, parada_id)
    if not parada or parada.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Parada no encontrada en su empresa.")
    # QA-06 (auditoría QA): la parada debe pertenecer a la planta activa.
    _validar_estacion_en_planta(parada.estacion_fk, context, db)
    if parada.origen != "PLANIFICADA":
        raise HTTPException(status_code=409, detail="Sólo se pueden eliminar paradas programadas (origen PLANIFICADA).")
    if parada.inicio <= datetime.utcnow():
        raise HTTPException(status_code=409, detail="No se puede eliminar una parada programada que ya comenzó o pasó.")

    db.delete(parada)
    db.commit()
    return {"mensaje": "Parada programada eliminada."}


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


def _validar_estacion_en_planta(estacion_id: uuid.UUID, context: TenantContext, db: Session) -> Estacion:
    """QA-06 (auditoría QA): validar_planta() sólo confirma que el
    usuario tiene acceso a la planta activa -- NO que la entidad que
    está escribiendo (parada, estación, asignación) pertenezca
    realmente a esa planta. Sin esto, un usuario con acceso legítimo a
    la Planta A podía escribir sobre una parada/estación/asignación de
    la Planta B del mismo tenant, con sólo conocer o adivinar el UUID
    (la lectura vía listados sí filtraba por planta -- el agujero era
    puntual en las escrituras por ID directo). Mismo criterio que
    _validar_linea_en_planta, para endpoints que reciben un UUID de
    estación en vez de un UUID de línea."""
    estacion = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada.")
    if estacion.linea_id:
        _validar_linea_en_planta(estacion.linea_id, context, db)
    return estacion


# ==========================================
# PLAN DE PRODUCCIÓN -- AVANZAR DE ORDEN (Fase AA, pedido de Green Mills)
# CRUD del Plan (crear/listar/ver/desactivar) vive en configuracion.py,
# junto al resto de los maestros. Esto es distinto: una ACCIÓN operativa
# (cambia qué orden clasifican los próximos escaneos en vivo), no una
# consulta ni una edición de config -- por eso vive acá, con el resto de
# las acciones de supervisor (clasificar_parada, asignar_dotacion).
# ==========================================
class PlanAvanzarResponse(BaseModel):
    plan_id: uuid.UUID
    estado: str
    orden_cerrada_id_orden: Optional[str] = None
    orden_activa_id_orden: Optional[str] = None
    orden_activa_sku_fk: Optional[str] = None


class AvanzarOrdenRequest(BaseModel):
    """Fase AX (auditoría de frontend, FE-P0-06): body opcional -- avanzar
    una orden completa/sobreproducida (o "iniciar plan", sin orden previa
    que cerrar) sigue sin pedir nada. Cerrar la orden activa con menos
    unidades reales que las esperadas sí lo exige, validado en el endpoint."""
    motivo: Optional[str] = None

    @field_validator("motivo")
    @classmethod
    def _limpiar_motivo(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        return v or None


@router.post("/planes/{plan_id}/avanzar-orden/", response_model=PlanAvanzarResponse)
def avanzar_orden(
    plan_id: uuid.UUID,
    payload: Optional[AvanzarOrdenRequest] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    """Cierra la orden activa del plan "en el estado en el que esté" --
    no valida cantidad producida contra la esperada, no hay nada que
    reconciliar: lo realmente producido siempre se calcula al vuelo
    sumando LiteEventoProduccion (ver _armar_plan_con_ordenes en
    configuracion.py), nunca se lee de un contador que haya que cuadrar
    a mano. Activa la siguiente orden por secuencia (siempre la que
    sigue, nunca una a elección -- confirmado con el usuario). Si no
    queda ninguna, el plan se cierra solo (también confirmado).

    Sólo Supervisor, Gerencia, Producción o SuperAdmin: es una acción
    operativa real que cambia en vivo qué SKU/tiempo ideal clasifican
    los próximos escaneos de la línea (ver resolver_orden_activa en
    clasificacion.py) -- ni un Operario ni un Encargado la disparan."""
    validar_planta(context, usuario, db)
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="Sólo Supervisor, Gerencia, Producción o SuperAdmin pueden avanzar un plan.")

    # QA-10 (auditoría QA): FOR UPDATE bloquea la fila del plan por el
    # resto de esta transacción -- dos requests concurrentes de
    # avanzar_orden sobre el MISMO plan (doble clic, doble tap) ya no
    # pueden leer el mismo estado "antes" y avanzar los dos: el segundo
    # espera a que el primero haga commit y recién ahí lee el estado ya
    # actualizado. Mismo patrón que scans.py usa para serializar por
    # estación (with_for_update()).
    plan = db.exec(
        select(PlanProduccion)
        .where(PlanProduccion.id == plan_id, PlanProduccion.tenant_id == context.tenant_id)
        .with_for_update()
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado.")
    if context.sub_tenant_id:
        _validar_linea_en_planta(plan.linea_id, context, db)
    # QA-01 (auditoría QA): con el modelo de 5 estados sólo tiene sentido
    # avanzar un plan EN_PROGRESO -- uno BORRADOR/PROGRAMADO todavía no
    # arrancó (falta activarlo primero, ver activar_plan en
    # configuracion.py) y uno CERRADO/CANCELADO ya terminó.
    if plan.estado == EstadoPlan.CERRADO:
        raise HTTPException(status_code=409, detail="El plan ya está cerrado -- no quedan más órdenes para avanzar.")
    if plan.estado == EstadoPlan.CANCELADO:
        raise HTTPException(status_code=409, detail="El plan está cancelado -- no se puede avanzar.")
    if plan.estado in (EstadoPlan.BORRADOR, EstadoPlan.PROGRAMADO):
        raise HTTPException(status_code=409, detail=f"El plan todavía no está en progreso (estado: {plan.estado.value}) -- activalo primero.")

    motivo = payload.motivo if payload else None

    orden_cerrada_id = None
    secuencia_actual = -1  # -1 = "todavía no arrancó" -> avanzar activa la primera de la secuencia
    if plan.orden_activa_fk:
        orden_actual = db.exec(
            select(OrdenProduccion).where(
                OrdenProduccion.id == plan.orden_activa_fk,
                OrdenProduccion.tenant_id == context.tenant_id,
            )
        ).first()
        if orden_actual:
            # Fase AX (FE-P0-06): "producido" siempre sumado de
            # LiteEventoProduccion -- misma regla de oro que
            # _armar_plan_con_ordenes (configuracion.py); OrdenProduccion.
            # cantidad_producida nunca se actualiza en ningún lado, leerla
            # acá daría siempre 0 y exigiría motivo hasta en una orden
            # completa. El backend valida esto server-side (no confía sólo
            # en que el front no deje enviar el botón sin texto) porque,
            # a diferencia de "cancelar un plan en_progreso" (Fase AV, el
            # backend sólo mira un estado), acá SÍ hace falta esta cuenta
            # para saber si hace falta motivo.
            producido = db.exec(
                select(func.sum(LiteEventoProduccion.unidades_procesadas)).where(
                    LiteEventoProduccion.tenant_id == context.tenant_id,
                    LiteEventoProduccion.orden_fk == orden_actual.id_orden,
                )
            ).first()
            producido = int(producido or 0)
            incompleta = producido < orden_actual.cantidad_esperada
            if incompleta and not motivo:
                raise HTTPException(
                    status_code=400,
                    detail=f"Motivo obligatorio: la orden se cierra con {producido} de {orden_actual.cantidad_esperada} unidades esperadas.",
                )
            orden_actual.estado = EstadoOrden.CERRADA
            orden_actual.motivo_incompleta = motivo if incompleta else None
            db.add(orden_actual)
            orden_cerrada_id = orden_actual.id_orden
            secuencia_actual = orden_actual.secuencia

    siguiente = db.exec(
        select(OrdenProduccion)
        .where(
            OrdenProduccion.tenant_id == context.tenant_id,
            OrdenProduccion.plan_id == plan.id,
            OrdenProduccion.secuencia > secuencia_actual,
            OrdenProduccion.estado != EstadoOrden.CERRADA,
            # QA-12 (auditoría QA): antes no exigía activo==true -- una
            # orden desactivada desde Configuración (baja lógica) podía
            # volver a activarse igual al avanzar el plan, como si nunca
            # se hubiera dado de baja.
            OrdenProduccion.activo == True,  # noqa: E712
        )
        .order_by(OrdenProduccion.secuencia)
    ).first()

    if siguiente:
        siguiente.estado = EstadoOrden.EN_PROGRESO
        db.add(siguiente)
        plan.orden_activa_fk = siguiente.id
    else:
        plan.orden_activa_fk = None
        plan.estado = EstadoPlan.CERRADO

    db.add(plan)
    db.commit()
    db.refresh(plan)

    return PlanAvanzarResponse(
        plan_id=plan.id, estado=plan.estado,
        orden_cerrada_id_orden=orden_cerrada_id,
        orden_activa_id_orden=siguiente.id_orden if siguiente else None,
        orden_activa_sku_fk=siguiente.sku_fk if siguiente else None,
    )


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
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para ver la dotación.")
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
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para asignar dotación.")

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
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para liberar una estación.")
    asignacion = db.exec(
        select(AsignacionTurno).where(AsignacionTurno.id == asignacion_id, AsignacionTurno.tenant_id == context.tenant_id)
    ).first()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")
    # QA-06 (auditoría QA): la asignación debe pertenecer a la planta
    # activa (vía su estación), no sólo al tenant.
    _validar_estacion_en_planta(asignacion.estacion_fk, context, db)
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
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para ver el monitor en vivo.")

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
# ASIGNACIÓN DE SUPERVISORES -- REGLA RECURRENTE (Fase Q)
# BACKEND_REQUIREMENTS.md §11.2. Path a nivel raíz (no bajo /supervisor)
# porque así lo especifica la doc.
#
# Reemplaza el modelo "un supervisor por línea+turno+día EXACTO" (Fase H)
# por una regla recurrente: qué días de la semana, con vigencia desde/
# hasta. Pueden coexistir varias reglas para la misma (línea, turno) a lo
# largo del tiempo -- por eso el POST ya no hace upsert por una clave
# implícita, siempre crea una regla nueva; para reemplazar una vigente se
# borra (DELETE) y se crea la que corresponda.
# ==========================================
def _dia_en_dias_semana(dia_iso: int, dias_semana: str) -> bool:
    """CSV de días ISO (1=lunes..7=domingo). Debe coincidir con la copia en
    analytics.py -- misma regla, sin dato compartido entre ambos routers
    hoy; si se toca acá, tocar también allá."""
    try:
        dias = {int(d) for d in dias_semana.split(",") if d.strip()}
    except (ValueError, AttributeError):
        return True
    return (not dias) or (dia_iso in dias)


def _dias_semana_a_set(dias_semana: str) -> set:
    """Vacío/corrupto = "todos los días" (mismo criterio que _dia_en_dias_semana)."""
    try:
        dias = {int(d) for d in dias_semana.split(",") if d.strip()}
    except (ValueError, AttributeError):
        return set(range(1, 8))
    return dias if dias else set(range(1, 8))


def _reglas_se_solapan(
    dias_a: str, desde_a: date, hasta_a: Optional[date],
    dias_b: str, desde_b: date, hasta_b: Optional[date],
) -> bool:
    """QA-16 (auditoría de reglas de negocio, Fase BL): dos reglas de
    asignación de supervisores conflictúan si comparten AL MENOS un día
    de la semana Y sus rangos de vigencia se superponen -- coexistir para
    la misma (línea, turno) es intencional (ver comentario de sección más
    arriba: ej. una regla para días hábiles y otra para el fin de
    semana), sólo es inválido si de verdad compiten por el mismo
    día/fecha."""
    if not (_dias_semana_a_set(dias_a) & _dias_semana_a_set(dias_b)):
        return False
    fin_a = hasta_a or date.max
    fin_b = hasta_b or date.max
    return desde_a <= fin_b and desde_b <= fin_a


class AsignacionSupervisorCreate(BaseModel):
    linea_id: uuid.UUID
    turno_id: uuid.UUID
    supervisor_id: uuid.UUID
    dias_semana: List[int] = Field(..., description="Días ISO en que aplica esta regla (1=lunes..7=domingo)")
    vigencia_desde: date
    vigencia_hasta: Optional[date] = Field(default=None, description="Vacío = sigue vigente indefinidamente")

    @field_validator("dias_semana")
    @classmethod
    def _validar_dias_semana(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("dias_semana no puede estar vacío.")
        invalidos = [d for d in v if d < 1 or d > 7]
        if invalidos:
            raise ValueError(f"Días inválidos: {invalidos}. Deben ser 1 (lunes) a 7 (domingo).")
        return sorted(set(v))


@router_asignaciones_supervisor.get("/supervisor/", response_model=List[AsignacionSupervisor])
def listar_asignaciones_supervisor(
    fecha: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    """Sin `fecha`: devuelve TODAS las reglas (para la pantalla de gestión
    en Configuración). Con `fecha`: filtra a las reglas vigentes ese día
    exacto (vigencia_desde/hasta + día de semana) -- por ejemplo, para
    resolver "quién cubre este turno tal día"."""
    validar_planta(context, usuario, db)
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para ver la asignación de supervisores.")
    query = select(AsignacionSupervisor).where(AsignacionSupervisor.tenant_id == context.tenant_id)
    if linea_id:
        _validar_linea_en_planta(linea_id, context, db)
        query = query.where(AsignacionSupervisor.linea_id == linea_id)
    reglas = db.exec(query).all()
    if fecha is None:
        return reglas
    dia_iso = fecha.isoweekday()
    return [
        r for r in reglas
        if r.vigencia_desde <= fecha
        and (r.vigencia_hasta is None or r.vigencia_hasta >= fecha)
        and _dia_en_dias_semana(dia_iso, r.dias_semana)
    ]


@router_asignaciones_supervisor.post("/supervisor/", response_model=AsignacionSupervisor, status_code=status.HTTP_201_CREATED)
def asignar_supervisor(
    payload: AsignacionSupervisorCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para asignar supervisores.")
    _validar_linea_en_planta(payload.linea_id, context, db)

    if payload.vigencia_hasta is not None and payload.vigencia_hasta < payload.vigencia_desde:
        raise HTTPException(status_code=400, detail="vigencia_hasta no puede ser anterior a vigencia_desde.")

    turno = db.exec(select(Turno).where(Turno.id == payload.turno_id, Turno.tenant_id == context.tenant_id)).first()
    if not turno:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")

    supervisor = db.exec(
        select(Supervisor).where(Supervisor.id == payload.supervisor_id, Supervisor.tenant_id == context.tenant_id, Supervisor.activo == True)  # noqa: E712
    ).first()
    if not supervisor:
        raise HTTPException(status_code=404, detail="Supervisor no encontrado o inactivo.")

    # Auditoría (reglas de negocio, Fase BL): antes el POST siempre creaba
    # sin comparar contra las reglas existentes -- podían coexistir dos
    # reglas para la misma (línea, turno) compitiendo por el mismo
    # día/fecha, sin que nada las rechazara (el front tampoco avisaba
    # antes de guardar). Coexistir para la misma línea+turno es
    # intencional cuando NO se superponen (ver comentario de sección),
    # así que el chequeo es específicamente por día+vigencia, no por
    # línea+turno solos.
    existentes = db.exec(
        select(AsignacionSupervisor).where(
            AsignacionSupervisor.tenant_id == context.tenant_id,
            AsignacionSupervisor.linea_id == payload.linea_id,
            AsignacionSupervisor.turno_id == payload.turno_id,
        )
    ).all()
    dias_nueva_csv = ",".join(str(d) for d in payload.dias_semana)
    for existente in existentes:
        if _reglas_se_solapan(
            dias_nueva_csv, payload.vigencia_desde, payload.vigencia_hasta,
            existente.dias_semana, existente.vigencia_desde, existente.vigencia_hasta,
        ):
            raise HTTPException(
                status_code=409,
                detail="Ya existe una regla de supervisión para esta línea y turno que se superpone en días y vigencia. Borrala primero o ajustá los días/fechas para que no se crucen.",
            )

    nueva = AsignacionSupervisor(
        tenant_id=context.tenant_id,
        linea_id=payload.linea_id,
        turno_id=payload.turno_id,
        supervisor_id=payload.supervisor_id,
        dias_semana=",".join(str(d) for d in payload.dias_semana),
        vigencia_desde=payload.vigencia_desde,
        vigencia_hasta=payload.vigencia_hasta,
    )
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva


@router_asignaciones_supervisor.delete("/supervisor/{asignacion_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_asignacion_supervisor(
    asignacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
):
    validar_planta(context, usuario, db)
    if usuario.rol not in ROLES_SUPERVISION_COMPLETA:
        raise HTTPException(status_code=403, detail="No tenés permisos para eliminar una asignación de supervisor.")
    regla = db.exec(
        select(AsignacionSupervisor).where(
            AsignacionSupervisor.id == asignacion_id,
            AsignacionSupervisor.tenant_id == context.tenant_id,
        )
    ).first()
    if not regla:
        raise HTTPException(status_code=404, detail="Regla de asignación no encontrada.")
    # QA-06 (auditoría QA): la regla debe pertenecer a la planta activa.
    _validar_linea_en_planta(regla.linea_id, context, db)
    db.delete(regla)
    db.commit()
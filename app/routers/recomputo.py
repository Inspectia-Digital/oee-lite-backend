"""Recómputo de eventos ya ingeridos (Fase S).

Motivo: el perfil de tiempos (SKU×Estación, SKU genérico, o el piso de
Línea -- Fase AC, ver clasificacion.resolver_umbrales_evento) sólo se lee
en /api/lite/scans, en el momento exacto del ping -- el resultado
(tiempo_ideal_seg, estado, ParadaDetectada.duracion_segundos) queda
congelado ahí para siempre (snapshot inmutable, ver
LiteEventoProduccion.tiempo_ideal_seg). Cargar un override o ajustar un
perfil DESPUÉS de que ya llegaron eventos no cambia nada retroactivamente
por sí solo -- de ahí este endpoint: reaplica la config VIGENTE sobre
eventos que YA existen, sin alterar los hechos inmutables de cada evento
(timestamp, orden_fk, unidades_procesadas).

Por qué no es un DELETE + reingesta: ni LiteEventoProduccion ni
ParadaDetectada de origen AUTOMATICA tienen endpoint de borrado en todo
este backend, a propósito -- se preserva trazabilidad real de downtime
(ver operacion.py::eliminar_parada_planificada). Este endpoint respeta la
misma regla: nunca borra, nunca toca una ParadaDetectada ya CLASIFICADA
por un supervisor.
"""
import uuid
from datetime import date, datetime, time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.auth import TenantContext, obtener_contexto_tenant_humano
from app.core.clasificacion import resolver_umbrales_evento
from app.core.database import get_session
from app.core.rbac import requerir_gerencia_o_superadmin
from app.models.domain import (
    Estacion, Linea, LiteEventoProduccion, OrdenProduccion, ParadaDetectada,
    EstadoParada, UsuarioSaaS,
)

router = APIRouter(prefix="/config/estaciones", tags=["Recómputo (Fase S)"])

# Guardrail defensivo (mismo criterio que MAX_FILAS_IMPORT_ERP en
# configuracion.py): esto no es un hot path, corre a pedido de
# Gerencia/SuperAdmin, pero sin límite un rango enorme podría mantener
# abierta una transacción larga sobre una tabla de alto volumen. Si hace
# falta más, se corre en varias tandas.
MAX_DIAS_RANGO = 90


class RecomputarEventosRequest(BaseModel):
    fecha_desde: date
    fecha_hasta: date


class RecomputarEventosResponse(BaseModel):
    eventos_recomputados: int
    eventos_sin_cambios: int
    paradas_creadas: int
    paradas_actualizadas: int
    paradas_ya_clasificadas_sin_tocar: int
    paradas_pendientes_obsoletas: List[uuid.UUID]


@router.post("/{estacion_id}/recomputar-eventos/", response_model=RecomputarEventosResponse)
def recomputar_eventos(
    estacion_id: uuid.UUID,
    payload: RecomputarEventosRequest,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    if payload.fecha_hasta < payload.fecha_desde:
        raise HTTPException(status_code=400, detail="fecha_hasta no puede ser anterior a fecha_desde.")
    if (payload.fecha_hasta - payload.fecha_desde).days > MAX_DIAS_RANGO:
        raise HTTPException(status_code=400, detail=f"El rango no puede superar los {MAX_DIAS_RANGO} días. Corré el recómputo en varias tandas.")

    estacion = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada.")

    linea = db.exec(select(Linea).where(Linea.id == estacion.linea_id)).first()

    desde_dt = datetime.combine(payload.fecha_desde, time.min)
    hasta_dt = datetime.combine(payload.fecha_hasta, time.max)

    eventos = db.exec(
        select(LiteEventoProduccion)
        .where(
            LiteEventoProduccion.tenant_id == context.tenant_id,
            LiteEventoProduccion.id_estacion == str(estacion_id),
            LiteEventoProduccion.timestamp >= desde_dt,
            LiteEventoProduccion.timestamp <= hasta_dt,
        )
        .order_by(LiteEventoProduccion.timestamp)
    ).all()

    # Evento inmediatamente anterior al rango: hace falta para el delta_t
    # del primer evento recomputado, pero él mismo NO se recomputa (queda
    # fuera del rango a propósito -- así se puede recomputar por tandas
    # sin reprocesar de nuevo lo ya hecho).
    evento_anterior = db.exec(
        select(LiteEventoProduccion)
        .where(
            LiteEventoProduccion.tenant_id == context.tenant_id,
            LiteEventoProduccion.id_estacion == str(estacion_id),
            LiteEventoProduccion.timestamp < desde_dt,
        )
        .order_by(LiteEventoProduccion.timestamp.desc())
    ).first()

    # sku_final de un evento ya ingerido se reconstruye desde su orden_fk
    # (inmutable) -> OrdenProduccion.sku_fk VIGENTE -- no hace falta (ni se
    # puede) re-derivar qué orden estaba EN_PROGRESO en su momento, eso ya
    # quedó fijado para siempre en orden_fk.
    cache_sku_por_orden: Dict[str, Optional[str]] = {}

    def _sku_de_orden(id_orden: Optional[str]) -> Optional[str]:
        if id_orden is None:
            return None
        if id_orden not in cache_sku_por_orden:
            orden = db.exec(
                select(OrdenProduccion).where(
                    OrdenProduccion.id_orden == id_orden,
                    OrdenProduccion.tenant_id == context.tenant_id,
                )
            ).first()
            cache_sku_por_orden[id_orden] = orden.sku_fk if orden else None
        return cache_sku_por_orden[id_orden]

    eventos_recomputados = 0
    eventos_sin_cambios = 0
    paradas_creadas = 0
    paradas_actualizadas = 0
    paradas_ya_clasificadas = 0
    paradas_obsoletas: List[uuid.UUID] = []

    for evento in eventos:
        sku_final = _sku_de_orden(evento.orden_fk)
        umbrales = resolver_umbrales_evento(db, context.tenant_id, estacion, linea, sku_final, evento.unidades_procesadas)

        nuevo_estado = "OPTIMO"
        nuevo_delta_t = 0.0
        nuevo_tiempo_perdido = 0.0
        mismo_contexto_de_orden = False

        if evento_anterior is not None:
            # Delta real recalculado desde los timestamps INMUTABLES -- no
            # desde el delta_t_segundos guardado, que pudo haber quedado
            # CAPEADO al tiempo ideal si el evento fue ALERTA en su momento
            # (mismo motivo que en scans.py: no penalizar Disponibilidad y
            # Rendimiento dos veces por el mismo hueco).
            nuevo_delta_t = (evento.timestamp - evento_anterior.timestamp).total_seconds()
            mismo_contexto_de_orden = evento_anterior.orden_fk == evento.orden_fk

            if mismo_contexto_de_orden:
                if nuevo_delta_t > umbrales.t_alerta:
                    nuevo_estado = "ALERTA"
                    nuevo_tiempo_perdido = nuevo_delta_t - umbrales.t_alerta
                    nuevo_delta_t = umbrales.tiempo_ideal_por_ciclo
                elif nuevo_delta_t > umbrales.t_lento:
                    nuevo_estado = "LENTO"
            # si cambió la orden: se guarda el delta real (arriba), pero no
            # se clasifica ni se toca ninguna parada -- mismo criterio de
            # continuidad de sesión que scans.py.

        cambio = (
            evento.tiempo_ideal_seg != umbrales.t_optimo
            or evento.estado != nuevo_estado
            or evento.delta_t_segundos != nuevo_delta_t
            or evento.tiempo_perdido_segundos != nuevo_tiempo_perdido
        )
        if cambio:
            evento.tiempo_ideal_seg = umbrales.t_optimo
            evento.estado = nuevo_estado
            evento.delta_t_segundos = nuevo_delta_t
            evento.tiempo_perdido_segundos = nuevo_tiempo_perdido
            db.add(evento)
            eventos_recomputados += 1
        else:
            eventos_sin_cambios += 1

        if evento_anterior is not None and mismo_contexto_de_orden:
            parada_existente = db.exec(
                select(ParadaDetectada).where(
                    ParadaDetectada.tenant_id == context.tenant_id,
                    ParadaDetectada.estacion_fk == estacion_id,
                    ParadaDetectada.origen == "AUTOMATICA",
                    ParadaDetectada.inicio == evento_anterior.timestamp,
                    ParadaDetectada.fin == evento.timestamp,
                )
            ).first()

            if nuevo_estado == "ALERTA":
                if parada_existente is None:
                    db.add(ParadaDetectada(
                        tenant_id=context.tenant_id, estacion_fk=estacion_id,
                        inicio=evento_anterior.timestamp, fin=evento.timestamp,
                        duracion_segundos=nuevo_tiempo_perdido, estado=EstadoParada.PENDIENTE,
                        origen="AUTOMATICA",
                    ))
                    paradas_creadas += 1
                elif parada_existente.estado == EstadoParada.PENDIENTE:
                    if parada_existente.duracion_segundos != nuevo_tiempo_perdido:
                        parada_existente.duracion_segundos = nuevo_tiempo_perdido
                        db.add(parada_existente)
                        paradas_actualizadas += 1
                else:  # CLASIFICADA -- nunca se toca
                    paradas_ya_clasificadas += 1
            elif parada_existente is not None:
                # El evento ya no califica ALERTA con la config vigente,
                # pero el hueco real igualmente ocurrió -- no hay forma de
                # borrar una parada AUTOMATICA (por diseño, ver docstring),
                # así que se deja intacta y se reporta para revisión manual
                # en vez de tocarla en silencio.
                if parada_existente.estado == EstadoParada.CLASIFICADA:
                    paradas_ya_clasificadas += 1
                else:
                    paradas_obsoletas.append(parada_existente.id)

        evento_anterior = evento

    db.commit()

    return RecomputarEventosResponse(
        eventos_recomputados=eventos_recomputados,
        eventos_sin_cambios=eventos_sin_cambios,
        paradas_creadas=paradas_creadas,
        paradas_actualizadas=paradas_actualizadas,
        paradas_ya_clasificadas_sin_tocar=paradas_ya_clasificadas,
        paradas_pendientes_obsoletas=paradas_obsoletas,
    )

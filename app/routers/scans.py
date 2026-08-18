import hashlib
import json
import logging
import re
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import uuid

from app.core.database import get_session
from app.core.auth_m2m import autenticar_dispositivo, ContextoDispositivo
from app.core.clasificacion import resolver_umbrales_evento, resolver_orden_activa
from app.core.errors import ErrorCode, error_terminal
from app.core.rate_limit import verificar_limite
from app.core.tiempo_planta import fecha_operativa_planta, fecha_local, hora_local
from app.core.turnos import resolver_turno_vigente
from app.models.domain import (
    Estacion, Linea, MaestroSKU, OrdenProduccion, Tenant, Planta,
    LiteEventoProduccion, ParadaDetectada, EstadoParada,
    ModoAsignacionOperariosEstacion, Operario, Turno, AsignacionTurno,
    Maquina, MaquinaEstacion, SesionOperario,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lite", tags=["Ingesta de Datos (Terminales y PLC)"])

TOLERANCIA_FUTURO = timedelta(minutes=5)
TOLERANCIA_ANTIGUEDAD = timedelta(days=7)
TIMEZONE_DEFAULT = "America/Buenos_Aires"


class ScanRequest(BaseModel):
    event_id: uuid.UUID = Field(..., description="UUIDv4 estable generado por el emisor, para idempotencia (Fase E1)")
    id_estacion: uuid.UUID = Field(..., description="UUID de la estación o máquina")
    codigo_pieza: Optional[str] = Field(None, description="Código de barras escaneado o nulo si es PLC")
    timestamp: Optional[datetime] = None
    maquina_id: Optional[uuid.UUID] = Field(None, description="Máquina física, si el hardware la reporta")
    unidades_rechazadas: int = Field(0, ge=0, description="Calidad por rechazo (Fase E2): 0 <= rechazadas <= unidades_procesadas")
    unidades_procesadas: Optional[int] = Field(
        None, ge=1,
        description=(
            "Fase P (PLC Green Mills): conteo edge-autoritativo. Cuando el "
            "tamaño del lote lo decide el propio emisor evento a evento (ej. "
            "un PLC con canales de formato fijo, donde 4 o 5 unidades "
            "dependen de qué canal basculó y no del SKU nominalmente activo "
            "en el sistema), se manda explícito acá y pisa la resolución por "
            "SKU de abajo. Si no viene, el comportamiento es exactamente el "
            "de antes (se resuelve por unidades_por_ciclo del SKU activo)."
        ),
    )


def _normalizar_timestamp_utc(ts: Optional[datetime], planta_timezone: Optional[str]) -> datetime:
    """Persistimos siempre UTC naive. Si el timestamp viene naive (sin offset),
    se interpreta en el timezone de la planta; si trae offset, se convierte."""
    if ts is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    if ts.tzinfo is None:
        try:
            tz = ZoneInfo(planta_timezone or TIMEZONE_DEFAULT)
        except Exception:
            tz = ZoneInfo(TIMEZONE_DEFAULT)
        ts_utc = ts.replace(tzinfo=tz).astimezone(timezone.utc)
    else:
        ts_utc = ts.astimezone(timezone.utc)

    return ts_utc.replace(tzinfo=None)


def _resolver_fecha_operativa(db: Session, estacion_id: str, ahora_utc: Optional[datetime] = None) -> date:
    """Fase DT (auditoría de backend, P0-04): fecha operativa LOCAL de la
    planta de esta estación -- antes login/logout de operario usaban
    `date.today()` directo (fecha del servidor, normalmente UTC),
    ignorando Planta.timezone. Cerca de medianoche local (turnos
    nocturnos sobre todo) eso podía loguear/desloguear contra el día
    calendario equivocado -- mismo patrón de bug que Fase BH ya corrigió
    en el frontend, nunca replicado acá.

    BE-P0-03 (PRD Go-Live Green Mills): la conversión en sí ya no vive
    acá -- se delega a app.core.tiempo_planta, compartida ahora con
    analytics.py y recomputo.py, para no tener tres copias del mismo
    ZoneInfo(planta.timezone) con el mismo fallback."""
    estacion = db.get(Estacion, uuid.UUID(estacion_id))
    linea = db.exec(select(Linea).where(Linea.id == estacion.linea_id)).first() if estacion else None
    planta = db.get(Planta, linea.planta_id) if linea else None
    return fecha_operativa_planta(planta, ahora_utc)


def _validar_rango_timestamp(ts_utc_naive: datetime) -> None:
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    if ts_utc_naive > ahora + TOLERANCIA_FUTURO:
        raise error_terminal(400, ErrorCode.TIMESTAMP_FUERA_DE_RANGO, "Timestamp más de 5 minutos en el futuro; rechazado.")
    if ts_utc_naive < ahora - TOLERANCIA_ANTIGUEDAD:
        raise error_terminal(400, ErrorCode.TIMESTAMP_FUERA_DE_RANGO, "Timestamp con más de 7 días de antigüedad; rechazado.")


def _calcular_payload_hash(scan: ScanRequest) -> str:
    """Hash canónico de los campos relevantes del payload (Fase E1, idempotencia)."""
    canonico = {
        "id_estacion": str(scan.id_estacion),
        "codigo_pieza": scan.codigo_pieza,
        "timestamp": scan.timestamp.isoformat() if scan.timestamp else None,
        "maquina_id": str(scan.maquina_id) if scan.maquina_id else None,
        "unidades_rechazadas": scan.unidades_rechazadas,
        "unidades_procesadas": scan.unidades_procesadas,
    }
    payload_json = json.dumps(canonico, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

@router.get("/estaciones/{estacion_id}/validar", response_model=Dict[str, Any])
def validar_estacion_terminal(
    estacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    dispositivo: ContextoDispositivo = Depends(autenticar_dispositivo)
):
    """(Bootstrap) Terminal/PLC solicita configuración de la estación y herencia de línea.
    Requiere API key M2M (Fase D.4a); ya no acepta JWT humano."""
    if str(estacion_id) != dispositivo.estacion_id:
        raise error_terminal(403, ErrorCode.ESTACION_NO_AUTORIZADA, "Esta credencial no está autorizada para esa estación.")

    estacion = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == dispositivo.tenant_id)).first()
    if not estacion:
        raise error_terminal(403, ErrorCode.ESTACION_NO_AUTORIZADA, "Estación no autorizada para esta credencial.")

    linea = db.exec(select(Linea).where(Linea.id == estacion.linea_id)).first()

    modo_asignacion = estacion.modo_asignacion_operarios
    if modo_asignacion == ModoAsignacionOperariosEstacion.HEREDAR and linea:
        modo_asignacion = linea.modo_asignacion_operarios

    # Bug real (encontrado al investigar por qué el kiosco nunca resolvía
    # turno): esta respuesta traía `estacion_id`/`estacion_nombre` planos y
    # `modo_asignacion_operarios` plano, sin `turnos` -- el frontend
    # (EstacionValidada, types/api.ts) siempre esperó `id`/`nombre`,
    # `configuracion.modo_asignacion_operarios` anidado y `turnos[]` (así
    # documentado en oee-lite/docs/BACKEND_REQUIREMENTS.md), y no existe
    # ninguna capa intermedia que traduzca -- apiFetch<EstacionValidada>
    # tipaba la respuesta cruda sin transformarla. En producción real
    # (fuera de modo mock) esto rompía la terminal en cascada: `est.id`
    # undefined viajaba como `id_estacion` en cada POST /scans (422 en
    # todo escaneo), y `est.configuracion` undefined hacía que el modo de
    # asignación cayera siempre al default "manual" del front, saltando en
    # silencio el paso de login de operario aunque la estación estuviera
    # configurada como "escaneo". Turnos de la línea a la que pertenece la
    # estación (mismo criterio de herencia que modo_asignacion_operarios:
    # el turno es de la línea, no de la estación puntual); sólo activos.
    turnos = db.exec(
        select(Turno)
        .where(
            Turno.tenant_id == dispositivo.tenant_id,
            Turno.linea_id == estacion.linea_id,
            Turno.activo == True,  # noqa: E712
        )
        .order_by(Turno.hora_inicio)
    ).all() if linea else []

    # FE-P0-03 (PRD Go-Live Green Mills): el BACKEND resuelve qué turno
    # está vigente, no la terminal. Antes el kiosco lo calculaba con
    # `now.getHours()` del dispositivo (TerminalPage.tsx) -- una tablet de
    # planta con el reloj mal configurado (o simplemente en otro huso)
    # fichaba al operario contra el turno equivocado, sin que nada lo
    # señalara. Acá se resuelve con la hora LOCAL DE LA PLANTA
    # (app.core.tiempo_planta) y la misma regla que usa Analytics
    # (app.core.turnos, incluidos los casos borde de turno nocturno).
    #
    # Va como campo de este endpoint y no como uno nuevo a propósito: la
    # terminal ya lo llama en el bootstrap, y hoy resuelve el turno una
    # sola vez en ese mismo momento -- misma semántica, sin request extra.
    # `None` = "sin turno vigente": la terminal pide selección manual.
    planta = db.get(Planta, linea.planta_id) if linea else None
    ahora_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    turno_vigente = resolver_turno_vigente(
        hora_local(ahora_utc_naive, planta),
        fecha_local(ahora_utc_naive, planta).isoweekday(),
        turnos,
    ) if turnos else None

    return {
        "id": str(estacion.id),
        "nombre": estacion.nombre,
        "tipo_produccion": linea.tipo_produccion if linea else "discreta",
        "configuracion": {
            "modo_asignacion_operarios": modo_asignacion,
        },
        "turno_vigente_id": str(turno_vigente.id) if turno_vigente else None,
        "turnos": [
            {
                "id": str(t.id),
                "nombre": t.nombre,
                "hora_inicio": t.hora_inicio.strftime("%H:%M"),
                "hora_fin": t.hora_fin.strftime("%H:%M"),
            }
            for t in turnos
        ],
        # Fase CM (auditoría de frontend, P0-02): el frontend necesita
        # saber a qué planta pertenece la estación de ESTE dispositivo
        # para poder comparar contra los permisos del humano logueado en
        # el navegador de la terminal -- la credencial M2M ya está
        # autorizada (chequeo de arriba), esto es sólo para que la UI no
        # deje operar a alguien sin asignación real a esa planta.
        "planta_id": str(linea.planta_id) if linea else None,
    }

@router.post("/scans", status_code=status.HTTP_201_CREATED)
def registrar_escaneo_rapido(
    scan: ScanRequest,
    db: Session = Depends(get_session),
    dispositivo: ContextoDispositivo = Depends(autenticar_dispositivo)
):
    """
    Motor DTR Universal: Procesa pings en < 50ms[cite: 14].
    Calcula Rendimiento, detecta micro-paradas y soporta lotes (Green Mills).
    Requiere API key M2M (Fase D.4a); ya no acepta JWT humano.
    """
    if str(scan.id_estacion) != dispositivo.estacion_id:
        raise error_terminal(403, ErrorCode.ESTACION_NO_AUTORIZADA, "Esta credencial no está autorizada para esa estación.")

    estacion = db.exec(select(Estacion).where(Estacion.id == scan.id_estacion, Estacion.tenant_id == dispositivo.tenant_id)).first()
    if not estacion:
        raise error_terminal(403, ErrorCode.ESTACION_NO_AUTORIZADA, "Estación no autorizada para esta credencial.")

    # Fase K (auditoría QA #5): serializa el procesamiento por estación.
    # Antes, la deduplicación por event_id y la lectura de "el último
    # evento" para calcular delta_t eran check-then-act sin lock: dos
    # requests concurrentes (reintentos de red del mismo PLC) podían pasar
    # ambas la comprobación de event_id y chocar recién en el commit
    # (IntegrityError -> 500 sin manejar), o leer el mismo "último evento"
    # y calcular deltas/paradas duplicadas. FOR UPDATE bloquea la fila de
    # la Estacion hasta el commit de este request; el siguiente que llegue
    # para la misma estación espera y ya ve el evento recién insertado.
    db.exec(select(Estacion).where(Estacion.id == estacion.id).with_for_update()).first()

    # 0. IDEMPOTENCIA (Fase E1): mismo event_id + mismo hash -> 200 con el
    # evento existente, sin duplicar. Mismo event_id + hash distinto -> 409.
    payload_hash = _calcular_payload_hash(scan)
    evento_existente = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.event_id == scan.event_id)
    ).first()
    if evento_existente:
        if evento_existente.payload_hash == payload_hash:
            # HANDOFF: mismo event_id + mismo hash -> 200 (no 201, no se crea nada nuevo).
            return JSONResponse(status_code=status.HTTP_200_OK, content={
                "status": "ok", "evento_id": str(evento_existente.id),
                "unidades": evento_existente.unidades_procesadas, "desempeno": evento_existente.estado,
                "idempotente": True,
            })
        raise error_terminal(409, ErrorCode.EVENTO_ID_CONFLICTO, "event_id ya usado con un payload diferente.")

    linea = db.exec(select(Linea).where(Linea.id == estacion.linea_id)).first()
    tenant_config = db.get(Tenant, dispositivo.tenant_id)
    planta = db.get(Planta, linea.planta_id) if linea else None

    # 1. RESOLUCIÓN DE TRAZABILIDAD (Regex Dinámico vs PLC Ciego)[cite: 14]
    orden_final = estacion.orden_activa_fk
    sku_final = estacion.sku_activo_fk

    if scan.codigo_pieza and tenant_config and tenant_config.regex_parser_orden:
        try:
            match = re.search(tenant_config.regex_parser_orden, scan.codigo_pieza.strip())
            if match:
                orden_final = match.group(1)
        except Exception:
            pass

    # 1.b FALLBACK: orden activa de la línea (Fase P, Fase AA). orden_activa_fk/
    # sku_activo_fk de la Estación nunca los escribe nadie (siguen NULL
    # siempre) -- confirmado al construir /analytics/linea-en-vivo/, que
    # usa esta misma resolución (resolver_orden_activa, clasificacion.py)
    # para lo que muestra en pantalla -- una sola fuente de verdad para
    # los dos. Para un PLC ciego (sin codigo_pieza, ej. Green Mills) esta
    # es la ÚNICA fuente posible de orden/SKU. No pisa un orden_final ya
    # resuelto por el regex de arriba -- sku_final sí se resuelve siempre
    # acá porque hoy no existe ningún otro camino que lo asigne.
    #
    # Fase AA: si la línea tiene un Plan de Producción ABIERTO con una
    # orden activa explícita (decidida por un supervisor vía
    # avanzar_orden), esa gana sobre el heurístico "EN_PROGRESO más
    # reciente" -- ver resolver_orden_activa. Líneas sin Plan siguen
    # exactamente igual que antes.
    if linea and (orden_final is None or sku_final is None):
        orden_activa = resolver_orden_activa(db, dispositivo.tenant_id, linea.id)
        if orden_activa:
            if orden_final is None:
                orden_final = orden_activa.id_orden
            if sku_final is None:
                sku_final = orden_activa.sku_fk

    # 2. FACTOR DE LOTE (Green Mills): sólo afecta unidades_a_sumar, se
    # resuelve acá porque necesita saber si la línea es por_lotes ANTES de
    # llamar a resolver_umbrales_evento (Fase S) -- esa función ya no
    # recalcula unidades, sólo tiempo ideal/tolerancia con las unidades que
    # se le pasan (las mismas que después quedan en unidades_procesadas).
    unidades_a_sumar = 1
    if sku_final and linea and linea.tipo_produccion == "por_lotes":
        sku_para_lote = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == sku_final, MaestroSKU.tenant_id == dispositivo.tenant_id)).first()
        if sku_para_lote:
            unidades_a_sumar = sku_para_lote.unidades_por_ciclo

    # 2.b CONTEO EDGE-AUTORITATIVO (Fase P): si el emisor ya sabe cuántas
    # unidades representa este evento (ver ScanRequest.unidades_procesadas),
    # pisa lo resuelto arriba por SKU. Se resuelve ANTES de resolver
    # umbrales/tolerancia -- esos tienen que reflejar cuántas unidades
    # produjo ESTE evento en particular.
    if scan.unidades_procesadas is not None:
        unidades_a_sumar = scan.unidades_procesadas

    # Fase P/Q/R/S, rediseñado en Fase AC (extraído a app/core/clasificacion.py
    # para que /config/estaciones/{id}/recomputar-eventos/ use exactamente
    # la misma cascada, nunca una reimplementación paralela): perfil de
    # tiempos (ideal/lento/alerta, siempre en segundos) resuelto
    # SKU×Estación > SKU > Línea -- ver docstring de resolver_umbrales_evento.
    umbrales = resolver_umbrales_evento(db, dispositivo.tenant_id, estacion, linea, sku_final, unidades_a_sumar)
    t_optimo, t_lento, t_alerta = umbrales.t_optimo, umbrales.t_lento, umbrales.t_alerta
    tiempo_ideal_por_ciclo = umbrales.tiempo_ideal_por_ciclo
    sku_resuelto = umbrales.sku_resuelto

    # 2.5 CALIDAD POR RECHAZO (Fase E2): 0 <= rechazadas <= procesadas.
    if scan.unidades_rechazadas > unidades_a_sumar:
        raise error_terminal(
            400, ErrorCode.CANTIDAD_RECHAZADA_INVALIDA,
            f"unidades_rechazadas ({scan.unidades_rechazadas}) no puede superar unidades_procesadas ({unidades_a_sumar}).",
        )

    # 3. CÁLCULO OEE DEDUCTIVO (Time-Based DTR)
    # Timezone (Fase E1): timestamp naive se interpreta con el timezone de la
    # planta; con offset se convierte. Siempre se persiste UTC naive.
    evento_timestamp = _normalizar_timestamp_utc(scan.timestamp, planta.timezone if planta else None)
    _validar_rango_timestamp(evento_timestamp)
    delta_t_segundos = 0.0
    tiempo_perdido_segundos = 0.0
    desempeno = "OPTIMO"

    ultimo_evento = db.exec(
        select(LiteEventoProduccion)
        .where(LiteEventoProduccion.id_estacion == str(estacion.id))
        .order_by(LiteEventoProduccion.timestamp.desc())
    ).first()

    if ultimo_evento:
        delta_t_segundos = (evento_timestamp - ultimo_evento.timestamp).total_seconds()

        # QA-04 (auditoría QA): un evento con timestamp ANTERIOR al último
        # persistido para esta estación (Edge/PLC que reenvía fuera de
        # orden dentro de la ventana de _validar_rango_timestamp, ej. tras
        # un corte de conectividad con buffer local) daba un delta
        # negativo. Antes de este fix eso pasaba de largo hasta el
        # commit, donde violaba el CHECK delta_t_segundos >= 0 (migración
        # 7af24f2546a7) -- y el `except IntegrityError` de más abajo
        # asumía SIEMPRE que una falla ahí era colisión de event_id, así
        # que devolvía "event_id ya usado con un payload diferente": un
        # mensaje totalmente engañoso para lo que en realidad era
        # desorden cronológico, con el evento perdido sin dejar rastro
        # claro para el gateway. Decisión del usuario: rechazar explícito
        # con un código distinguible -- no se intenta reordenar ni
        # recomputar automáticamente acá.
        if delta_t_segundos < 0:
            raise error_terminal(
                status.HTTP_409_CONFLICT, ErrorCode.EVENTO_FUERA_DE_ORDEN,
                (
                    "EVENTO_FUERA_DE_ORDEN: el timestamp del evento es anterior al último "
                    f"registrado para esta estación ({ultimo_evento.timestamp.isoformat()}Z). "
                    "No se persiste -- reenviar en orden cronológico."
                ),
            )

        # Fase Q (feedback de producto): un cambio de ORDEN ACTIVA entre el
        # evento anterior y éste es un límite de SESIÓN, no una parada real
        # -- una orden tiene día/turno de INICIO pero no de fin definido,
        # puede seguir corriendo más allá de su turno nominal (horas
        # extra, cambio de turno sin cortar producción). El límite real de
        # "sigue siendo la misma sesión de producción" es la continuidad
        # de la orden, no el reloj/turno. Mismo trato que el primer evento
        # de la estación (sin ultimo_evento): no se evalúa contra los
        # umbrales, no genera parada. Si ambas son None (estación que
        # nunca resuelve orden) el comportamiento no cambia respecto a
        # antes de este fix.
        mismo_contexto_de_orden = ultimo_evento.orden_fk == orden_final

        if not mismo_contexto_de_orden:
            pass  # arranca sesión nueva: se guarda delta_t real, pero no se clasifica ni genera parada
        elif delta_t_segundos > t_alerta:
            desempeno = "ALERTA"
            # Disponibilidad (Fase E2): sólo el EXCEDENTE sobre la tolerancia
            # cuenta como tiempo perdido -- no el delta completo, para no
            # penalizar dos veces (Disponibilidad Y Rendimiento) por el mismo hueco.
            tiempo_perdido_segundos = delta_t_segundos - t_alerta

            # Fase AF: id UUID de la orden activa (si hay una resuelta),
            # para poder analizar paradas por SKU más adelante -- orden_final
            # es el id_orden (string, legacy) que ya se usó para clasificar
            # este evento; acá se resuelve el UUID real (.id) una sola vez,
            # sólo en el momento en que efectivamente se crea una parada
            # (no en cada scan), mismo criterio C1/C2 que el resto de las
            # FKs nuevas desde Fase AA.
            orden_activa_id = None
            if orden_final is not None:
                orden_activa_obj = db.exec(
                    select(OrdenProduccion).where(
                        OrdenProduccion.tenant_id == dispositivo.tenant_id,
                        OrdenProduccion.id_orden == orden_final,
                    )
                ).first()
                orden_activa_id = orden_activa_obj.id if orden_activa_obj else None

            # Disparar Parada Automática[cite: 13]
            nueva_parada = ParadaDetectada(
                tenant_id=dispositivo.tenant_id,
                estacion_fk=estacion.id,
                inicio=ultimo_evento.timestamp,
                fin=evento_timestamp,
                duracion_segundos=tiempo_perdido_segundos,
                estado=EstadoParada.PENDIENTE,
                orden_fk=orden_activa_id,
            )
            db.add(nueva_parada)
            # Cap de Rendimiento: limitamos delta_t al tiempo ideal de UN
            # CICLO (no de una unidad -- mismo motivo que t_lento/t_alerta
            # arriba) para no penalizar dos veces el mismo hueco.
            delta_t_segundos = tiempo_ideal_por_ciclo

        elif delta_t_segundos > t_lento:
            desempeno = "LENTO"

    # 3.5 MÁQUINA (Fase E1): si el hardware informa una máquina no asociada a
    # la estación, se acepta el evento igual con maquina_id=NULL + warning
    # estructurado (nunca se rechaza el evento por esto).
    maquina_id_final = None
    if scan.maquina_id:
        asociacion = db.exec(
            select(MaquinaEstacion).where(
                MaquinaEstacion.maquina_id == scan.maquina_id,
                MaquinaEstacion.estacion_id == estacion.id,
                MaquinaEstacion.tenant_id == dispositivo.tenant_id,
                MaquinaEstacion.activo == True,  # noqa: E712
            )
        ).first()
        if asociacion:
            maquina_id_final = scan.maquina_id
        else:
            logger.warning(
                "maquina_id inconsistente: %s no está asociada a la estación %s (tenant %s). "
                "Evento aceptado con maquina_id=NULL.",
                scan.maquina_id, estacion.id, dispositivo.tenant_id,
            )

    # 4. Persistencia Edge-Priority[cite: 14]
    nuevo_evento = LiteEventoProduccion(
        tenant_id=dispositivo.tenant_id,
        id_estacion=str(estacion.id),
        codigo_pieza=scan.codigo_pieza,
        orden_fk=orden_final,
        cantidad_producida=1,
        unidades_procesadas=unidades_a_sumar, # Multiplicador guardado inmutable
        timestamp=evento_timestamp,
        delta_t_segundos=delta_t_segundos,
        tiempo_perdido_segundos=tiempo_perdido_segundos,
        estado=desempeno,
        maquina_id=maquina_id_final,
        event_id=scan.event_id,
        payload_hash=payload_hash,
        unidades_rechazadas=scan.unidades_rechazadas,
        # Snapshot inmutable (Fase E2): tiempo ideal POR UNIDAD vigente en
        # este momento (SKU activo si había uno, si no el de la estación).
        # Rendimiento = tiempo_ideal_seg * unidades_procesadas.
        tiempo_ideal_seg=t_optimo,
        # Snapshot inmutable (Fase E1): refleja el estado de la estación en
        # el momento del evento; no se recalcula si la estación cambia después.
        incluido_oee=estacion.activa,
    )

    db.add(nuevo_evento)
    try:
        db.commit()
    except IntegrityError:
        # Red de seguridad además del FOR UPDATE de arriba: si de todos
        # modos hubo una colisión de event_id (p.ej. dos requests que
        # entraron a la sección crítica antes de que el lock surtiera
        # efecto), no se cuelga en un 500 -- se resuelve como el mismo
        # caso idempotente de la comprobación inicial.
        db.rollback()
        evento_existente = db.exec(
            select(LiteEventoProduccion).where(LiteEventoProduccion.event_id == scan.event_id)
        ).first()
        if evento_existente and evento_existente.payload_hash == payload_hash:
            return JSONResponse(status_code=status.HTTP_200_OK, content={
                "status": "ok", "evento_id": str(evento_existente.id),
                "unidades": evento_existente.unidades_procesadas, "desempeno": evento_existente.estado,
                "idempotente": True,
            })
        raise error_terminal(409, ErrorCode.EVENTO_ID_CONFLICTO, "event_id ya usado con un payload diferente.")

    return {"status": "ok", "evento_id": nuevo_evento.id, "unidades": unidades_a_sumar, "desempeno": desempeno}


# ==========================================
# LOGIN DE OPERARIO POR ESCANEO (Fase D.4b)
# ==========================================
class OperarioLoginRequest(BaseModel):
    legajo: str = Field(..., description="Legajo escaneado del operario")
    turno_fk: uuid.UUID = Field(
        ...,
        description=(
            "Turno vigente. Se pide explícito y no se auto-resuelve por hora: "
            "aunque Fase E1 ya normaliza timestamps con timezone de planta, "
            "auto-resolver 'qué turno corresponde ahora' (turnos nocturnos que "
            "cruzan medianoche, empates en los bordes) es una decisión de "
            "producto aparte, no incluida todavía."
        ),
    )


@router.post("/operario/login", status_code=status.HTTP_200_OK)
def login_operario_terminal(
    payload: OperarioLoginRequest,
    db: Session = Depends(get_session),
    dispositivo: ContextoDispositivo = Depends(autenticar_dispositivo),
):
    """
    El dispositivo (terminal de mano) ya se autenticó vía API key
    (X-Device-Key, Fase D.4a). El operario se identifica escaneando su
    legajo una vez por sesión/turno. Esto NO toca eventos históricos: crea
    o actualiza la fila de AsignacionTurno para tenant + estación (la de la
    credencial) + turno + fecha de hoy -- dotación/staffing PLANIFICADO,
    sigue igual que siempre.

    BE-P0-06 (PRD Go-Live Green Mills): en paralelo, ABRE una
    SesionOperario nueva -- nunca pisa una fila existente como hacía
    AsignacionTurno arriba. Si había una sesión abierta en esta estación
    (de este u otro operario, ej. el turno anterior nunca escaneó
    [SALIR]), se cierra primero (`salida = ahora`) para que los
    intervalos de sesión nunca se superpongan. La atribución de eventos
    a operario (KPI, ver analytics.py) se resuelve contra el historial
    de SesionOperario, no contra AsignacionTurno.
    """
    operario = db.exec(
        select(Operario).where(
            Operario.tenant_id == dispositivo.tenant_id,
            Operario.legajo == payload.legajo,
            Operario.activo == True,  # noqa: E712
        )
    ).first()
    if not operario:
        # Fase BY: sólo se cuenta acá, en el legajo que NO matcheó -- un
        # turno con muchos operarios badgeando en simultáneo (éxito) no
        # debe frenarse; barrer legajos por fuerza bruta sí. El
        # dispositivo ya está autenticado M2M (autenticar_dispositivo,
        # que además ya limita intentos de credencial inválida por IP).
        verificar_limite(
            db, f"login_operario:{dispositivo.tenant_id}:{dispositivo.estacion_id}",
            max_intentos=20, ventana_segundos=60,
        )
        raise error_terminal(404, ErrorCode.OPERARIO_NO_ENCONTRADO, "Legajo no encontrado o inactivo.")

    turno = db.exec(
        select(Turno).where(Turno.id == payload.turno_fk, Turno.tenant_id == dispositivo.tenant_id)
    ).first()
    if not turno:
        raise error_terminal(404, ErrorCode.TURNO_NO_ENCONTRADO, "Turno no encontrado.")

    hoy = _resolver_fecha_operativa(db, dispositivo.estacion_id)
    asignacion = db.exec(
        select(AsignacionTurno).where(
            AsignacionTurno.tenant_id == dispositivo.tenant_id,
            AsignacionTurno.estacion_fk == uuid.UUID(dispositivo.estacion_id),
            AsignacionTurno.turno_fk == payload.turno_fk,
            AsignacionTurno.fecha == hoy,
        )
    ).first()

    if asignacion:
        asignacion.operario_fk = operario.id
        # Fase BZ: un login nuevo reabre la sesión -- si el operario
        # anterior había hecho [SALIR] en este mismo turno/estación/día,
        # ese hora_salida ya no aplica al operario que acaba de fichar.
        asignacion.hora_salida = None
        db.add(asignacion)
    else:
        asignacion = AsignacionTurno(
            tenant_id=dispositivo.tenant_id,
            fecha=hoy,
            estacion_fk=uuid.UUID(dispositivo.estacion_id),
            operario_fk=operario.id,
            turno_fk=payload.turno_fk,
        )
        db.add(asignacion)

    ahora_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    sesion_abierta = db.exec(
        select(SesionOperario).where(
            SesionOperario.tenant_id == dispositivo.tenant_id,
            SesionOperario.estacion_fk == uuid.UUID(dispositivo.estacion_id),
            SesionOperario.salida.is_(None),
        )
    ).first()
    if sesion_abierta:
        sesion_abierta.salida = ahora_utc
        db.add(sesion_abierta)

    nueva_sesion = SesionOperario(
        tenant_id=dispositivo.tenant_id,
        operario_fk=operario.id,
        estacion_fk=uuid.UUID(dispositivo.estacion_id),
        turno_fk=payload.turno_fk,
        entrada=ahora_utc,
    )
    db.add(nueva_sesion)

    db.commit()
    db.refresh(asignacion)
    return {
        "mensaje": f"Operario '{operario.nombre_completo}' logueado en esta terminal para el turno actual.",
        "asignacion_id": asignacion.id,
    }


class OperarioLogoutRequest(BaseModel):
    turno_fk: uuid.UUID = Field(..., description="Turno de la sesión que se está cerrando.")


@router.post("/operario/logout", status_code=status.HTTP_200_OK)
def logout_operario_terminal(
    payload: OperarioLogoutRequest,
    db: Session = Depends(get_session),
    dispositivo: ContextoDispositivo = Depends(autenticar_dispositivo),
):
    """Fase BZ (auditoría de robustez): formaliza [SALIR] -- antes ese
    botón de la terminal sólo reseteaba estado LOCAL del front, sin
    ningún registro del lado del backend. Marca `hora_salida` en la
    AsignacionTurno de hoy para esta estación+turno (la misma fila que
    crea/actualiza login_operario_terminal) -- dotación/staffing
    PLANIFICADO, sigue igual que siempre.

    BE-P0-06 (PRD Go-Live Green Mills): en paralelo, cierra
    (`salida = ahora`) la SesionOperario ABIERTA de esta estación, que
    es lo que ahora gobierna la atribución real de eventos a operario
    (ver analytics.py) -- ya no la AsignacionTurno de arriba."""
    hoy = _resolver_fecha_operativa(db, dispositivo.estacion_id)
    asignacion = db.exec(
        select(AsignacionTurno).where(
            AsignacionTurno.tenant_id == dispositivo.tenant_id,
            AsignacionTurno.estacion_fk == uuid.UUID(dispositivo.estacion_id),
            AsignacionTurno.turno_fk == payload.turno_fk,
            AsignacionTurno.fecha == hoy,
        )
    ).first()
    if not asignacion:
        raise error_terminal(404, ErrorCode.SESION_NO_ENCONTRADA, "No hay ninguna sesión abierta para cerrar en esta estación/turno hoy.")

    ahora_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    asignacion.hora_salida = ahora_utc
    db.add(asignacion)

    sesion_abierta = db.exec(
        select(SesionOperario).where(
            SesionOperario.tenant_id == dispositivo.tenant_id,
            SesionOperario.estacion_fk == uuid.UUID(dispositivo.estacion_id),
            SesionOperario.salida.is_(None),
        )
    ).first()
    if sesion_abierta:
        sesion_abierta.salida = ahora_utc
        db.add(sesion_abierta)

    db.commit()
    return {"mensaje": "Sesión cerrada.", "asignacion_id": asignacion.id}
"""BE-P0-03 (PRD Go-Live Green Mills, auditoría backend 18/8): resolución
de fecha/hora OPERATIVA por timezone IANA de la planta, unificada en un
solo lugar.

Antes de esta fase existían implementaciones paralelas de la misma idea
-- ZoneInfo(planta.timezone) con el mismo try/except de fallback --
repetidas en scans.py (_resolver_fecha_operativa, sólo para login/logout
de operario, Fase DT) y en analytics.py (_fecha_planta/_hora_planta/
obtener_rango_dia), y ninguna en recomputo.py (que hacía datetime.combine
naive, ignorando la planta por completo -- ver BE-P0-05).

Esa duplicación escondía un bug real y sistémico: varios endpoints de
analytics.py resolvían "hoy" con `datetime.utcnow().date()` -- la fecha
del RELOJ UTC DEL SERVIDOR -- antes de siquiera intentar convertir a la
timezone de la planta. Cerca de medianoche UTC (21hs en Buenos Aires,
UTC-3) eso devuelve el día calendario SIGUIENTE al que realmente está
corriendo en la planta, sin importar que el resto de la función supiera
convertir bien una fecha ya explícita.
"""
from datetime import date, datetime, time, timezone as dt_timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

TIMEZONE_DEFAULT = "America/Buenos_Aires"


def resolver_timezone_planta(planta) -> ZoneInfo:
    """ZoneInfo de la planta, con fallback seguro a TIMEZONE_DEFAULT si
    no tiene timezone configurado, el valor guardado no es un IANA
    válido, o directamente no hay planta (contexto sin sub_tenant_id).
    Fase DP ya valida el timezone al crear/editar una Planta, pero esta
    función no confía ciegamente en filas viejas creadas antes de esa
    validación."""
    try:
        return ZoneInfo(planta.timezone) if planta and planta.timezone else ZoneInfo(TIMEZONE_DEFAULT)
    except Exception:
        return ZoneInfo(TIMEZONE_DEFAULT)


def fecha_operativa_planta(planta, ahora_utc: Optional[datetime] = None) -> date:
    """"Hoy" según el reloj de la PLANTA, no el reloj UTC del servidor.

    `ahora_utc` es inyectable (default: instante real) a propósito --
    mismo criterio que el resto de las funciones de resolución horaria
    de este backend (ver _resolver_turno_por_horario en analytics.py):
    permite tests unitarios deterministas sin mockear datetime.now()."""
    if ahora_utc is None:
        ahora_utc = datetime.now(dt_timezone.utc)
    elif ahora_utc.tzinfo is None:
        ahora_utc = ahora_utc.replace(tzinfo=dt_timezone.utc)
    return ahora_utc.astimezone(resolver_timezone_planta(planta)).date()


def fecha_local(ts_utc_naive: datetime, planta) -> date:
    """Convierte un timestamp UTC naive (como se persisten los eventos,
    ver LiteEventoProduccion.timestamp) a la fecha calendario de la
    PLANTA -- para un instante arbitrario, no "ahora" (ver
    fecha_operativa_planta arriba)."""
    return ts_utc_naive.replace(tzinfo=dt_timezone.utc).astimezone(resolver_timezone_planta(planta)).date()


def hora_local(ts_utc_naive: datetime, planta) -> time:
    """Hora LOCAL de planta de un timestamp (naive UTC) -- para resolver
    contra Turno.hora_inicio/hora_fin, que se cargan en hora de planta,
    nunca en UTC."""
    return ts_utc_naive.replace(tzinfo=dt_timezone.utc).astimezone(resolver_timezone_planta(planta)).time()


def rango_utc_dia_local(fecha: date, planta) -> Tuple[datetime, datetime]:
    """[medianoche, 23:59:59.999999] del día `fecha` en hora LOCAL de la
    planta, devuelto en UTC naive (mismo formato en que se persisten los
    timestamps) para comparar directo contra LiteEventoProduccion.timestamp."""
    return rango_utc_multi_dia_local(fecha, fecha, planta)


def rango_utc_multi_dia_local(fecha_desde: date, fecha_hasta: date, planta) -> Tuple[datetime, datetime]:
    """Igual que rango_utc_dia_local pero para un rango [fecha_desde,
    fecha_hasta] inclusive -- medianoche local del primer día a 23:59:59
    local del último, ambos convertidos a UTC naive.

    BE-P0-05: recomputar_eventos (recomputo.py) armaba este rango con
    datetime.combine naive, tratando la fecha pedida como si ya fuera
    UTC -- para una planta en UTC-3 eso pierde las últimas 3 horas del
    día local pedido (caen en el UTC del día siguiente) y arrastra 3
    horas de la noche local del día anterior."""
    tz = resolver_timezone_planta(planta)
    inicio_local = datetime.combine(fecha_desde, time.min, tzinfo=tz)
    fin_local = datetime.combine(fecha_hasta, time.max, tzinfo=tz)
    return (
        inicio_local.astimezone(dt_timezone.utc).replace(tzinfo=None),
        fin_local.astimezone(dt_timezone.utc).replace(tzinfo=None),
    )

"""Fase DT (auditoría de backend, P0-04): _resolver_fecha_operativa
(login/logout de operario en Terminal) usa Planta.timezone en vez de
`date.today()` del servidor -- antes un turno nocturno cerca de
medianoche local podía loguear/desloguear contra el día calendario
equivocado si el servidor corre en UTC (u otro timezone distinto al de
la planta).

Tests unitarios deterministas (mismo criterio que QA-05,
test_qa05_turno_nocturno.py: `ahora_utc` inyectado, sin wall-clock real
ni mocks de datetime.now())."""
from datetime import date, datetime, timezone

from app.models.domain import Estacion, Linea, Planta
from app.routers.scans import _resolver_fecha_operativa


def _preparar_estacion(db, tenant_id, planta_timezone):
    planta = Planta(tenant_id=tenant_id, nombre="Planta DT", timezone=planta_timezone)
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea DT")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación DT", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    return planta, estacion


def test_turno_nocturno_no_salta_de_dia_con_servidor_en_utc(db, tenant_a):
    """Planta en America/Buenos_Aires (UTC-3). 2026-08-18 02:00 UTC =
    2026-08-17 23:00 local -- todavía lunes 17 para la planta, aunque el
    servidor (UTC) ya esté en martes 18. `date.today()` habría devuelto
    el 18; la fecha operativa correcta es el 17."""
    _planta, estacion = _preparar_estacion(db, tenant_a, "America/Buenos_Aires")
    ahora_utc = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)

    fecha = _resolver_fecha_operativa(db, str(estacion.id), ahora_utc=ahora_utc)

    assert fecha == date(2026, 8, 17)


def test_turno_nocturno_ya_cruzo_medianoche_local(db, tenant_a):
    """Mismo escenario pero una hora más tarde: 2026-08-18 04:00 UTC =
    2026-08-18 01:00 local -- ahí sí ya es martes 18 para la planta."""
    _planta, estacion = _preparar_estacion(db, tenant_a, "America/Buenos_Aires")
    ahora_utc = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)

    fecha = _resolver_fecha_operativa(db, str(estacion.id), ahora_utc=ahora_utc)

    assert fecha == date(2026, 8, 18)


def test_timezone_distinta_a_utc3_tambien_se_respeta(db, tenant_a):
    """Planta en Europe/Madrid (UTC+2 en agosto, horario de verano).
    2026-08-17 23:30 UTC = 2026-08-18 01:30 local -- ya es el día
    siguiente para esa planta, aunque siga siendo el 17 en UTC."""
    _planta, estacion = _preparar_estacion(db, tenant_a, "Europe/Madrid")
    ahora_utc = datetime(2026, 8, 17, 23, 30, tzinfo=timezone.utc)

    fecha = _resolver_fecha_operativa(db, str(estacion.id), ahora_utc=ahora_utc)

    assert fecha == date(2026, 8, 18)


def test_sin_ahora_utc_inyectado_usa_el_instante_real(db, tenant_a):
    """Regresión de wiring: sin `ahora_utc` explícito (el caso real de
    login/logout), la función sigue funcionando contra el reloj real --
    sólo se verifica que no explota y devuelve una fecha razonable
    (mismo día UTC +/-1, nunca None ni una excepción)."""
    _planta, estacion = _preparar_estacion(db, tenant_a, "America/Buenos_Aires")

    fecha = _resolver_fecha_operativa(db, str(estacion.id))

    hoy_utc = datetime.now(timezone.utc).date()
    assert abs((fecha - hoy_utc).days) <= 1

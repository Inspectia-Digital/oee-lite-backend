"""BE-P0-03/BE-P0-05 (PRD Go-Live Green Mills, auditoría 18/8): resolución
de fecha/hora operativa unificada en app.core.tiempo_planta, y los dos
bugs reales que la falta de unificación escondía:

1. obtener_rango_dia (analytics.py) resolvía "hoy" con
   datetime.utcnow().date() -- el reloj UTC del SERVIDOR -- ANTES de
   siquiera intentar convertir a la timezone de la planta. Cerca de
   medianoche UTC (21hs en Buenos Aires, UTC-3) eso arrancaba con el día
   calendario SIGUIENTE al que realmente corre en la planta.

2. recomputar_eventos (recomputo.py) armaba el rango UTC con
   datetime.combine naive -- trataba fecha_desde/fecha_hasta como si ya
   fueran UTC, ignorando la planta por completo. Para Buenos Aires
   (UTC-3) eso pierde las últimas 3 horas del día local pedido.

Todos los tests unitarios usan ahora_utc/timestamps fijos (nunca
wall-clock real). La única excepción es el test de integración de
recomputar_eventos: usa "ayer" relativo a la hora real porque el propio
endpoint de ingesta (scans.py::_validar_rango_timestamp) rechaza
timestamps a más de 5 minutos en el futuro o más de 7 días de
antigüedad -- una fecha fija se volvería inválida con el correr de los
días."""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from app.core.auth import TenantContext
from app.core.tiempo_planta import (
    TIMEZONE_DEFAULT,
    fecha_local,
    fecha_operativa_planta,
    hora_local,
    rango_utc_dia_local,
    rango_utc_multi_dia_local,
    resolver_timezone_planta,
)
from app.models.domain import Estacion, Linea, Planta
from app.routers.analytics import obtener_rango_dia
from tests.conftest import autenticar_como


# ---------------------------------------------------------------------
# Unidad: app/core/tiempo_planta.py
# ---------------------------------------------------------------------

def test_resolver_timezone_planta_usa_default_si_no_hay_planta():
    assert str(resolver_timezone_planta(None)) == TIMEZONE_DEFAULT


def test_resolver_timezone_planta_usa_default_si_timezone_invalido():
    planta = Planta(tenant_id=str(uuid.uuid4()), nombre="X", timezone="No/Existe")
    assert str(resolver_timezone_planta(planta)) == TIMEZONE_DEFAULT


def test_fecha_operativa_planta_no_salta_de_dia_cerca_de_medianoche_utc():
    """2026-08-18 02:00 UTC = 2026-08-17 23:00 en Buenos Aires (UTC-3) --
    todavía lunes 17 para la planta."""
    planta = Planta(tenant_id=str(uuid.uuid4()), nombre="X", timezone="America/Buenos_Aires")
    ahora_utc = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    assert fecha_operativa_planta(planta, ahora_utc) == date(2026, 8, 17)


def test_fecha_local_y_hora_local_convierten_timestamp_naive_utc():
    planta = Planta(tenant_id=str(uuid.uuid4()), nombre="X", timezone="America/Buenos_Aires")
    ts_naive_utc = datetime(2026, 8, 18, 2, 30)  # naive, como se persisten los eventos
    assert fecha_local(ts_naive_utc, planta) == date(2026, 8, 17)
    assert hora_local(ts_naive_utc, planta) == time(23, 30)


def test_rango_utc_dia_local_arranca_3_horas_despues_de_medianoche_utc():
    """El día local 2026-08-18 en Buenos Aires (UTC-3) va de 2026-08-18
    03:00 UTC a 2026-08-19 02:59:59.999999 UTC -- NUNCA [00:00, 23:59:59]
    UTC puro, que sería el día UTC, no el día de planta."""
    planta = Planta(tenant_id=str(uuid.uuid4()), nombre="X", timezone="America/Buenos_Aires")
    inicio, fin = rango_utc_dia_local(date(2026, 8, 18), planta)
    assert inicio == datetime(2026, 8, 18, 3, 0)
    assert fin.date() == date(2026, 8, 19)
    assert fin.time() == time(2, 59, 59, 999999)


def test_rango_utc_multi_dia_local_cubre_desde_medianoche_local_primer_dia_a_fin_del_ultimo():
    planta = Planta(tenant_id=str(uuid.uuid4()), nombre="X", timezone="America/Buenos_Aires")
    inicio, fin = rango_utc_multi_dia_local(date(2026, 8, 18), date(2026, 8, 19), planta)
    assert inicio == datetime(2026, 8, 18, 3, 0)
    assert fin.date() == date(2026, 8, 20)
    assert fin.time() == time(2, 59, 59, 999999)


# ---------------------------------------------------------------------
# BE-P0-03: obtener_rango_dia -- el fallback de "hoy" ya no es UTC puro
# ---------------------------------------------------------------------

def _preparar_planta(db, tenant_id, tz="America/Buenos_Aires"):
    planta = Planta(tenant_id=tenant_id, nombre="Planta BE-P0-03", timezone=tz)
    db.add(planta)
    db.commit()
    db.refresh(planta)
    return planta


def test_obtener_rango_dia_sin_fecha_explicita_usa_hoy_de_la_planta_no_utc(db, tenant_a):
    """El caso que antes fallaba: 2026-08-18 01:00 UTC = 2026-08-17 22:00
    en Buenos Aires -- todavía domingo/lunes 17 para la planta, aunque
    datetime.utcnow().date() (el bug viejo) ya hubiera devuelto el 18."""
    planta = _preparar_planta(db, tenant_a)
    context = TenantContext(tenant_id=tenant_a, sub_tenant_id=str(planta.id))
    ahora_utc = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)

    inicio, fin = obtener_rango_dia(None, context, db, ahora_utc=ahora_utc)

    # El rango correcto es el del 17 de agosto en Buenos Aires: [17 03:00, 18 02:59:59.999999] UTC.
    assert inicio == datetime(2026, 8, 17, 3, 0)
    assert fin.date() == date(2026, 8, 18)
    assert fin.time() == time(2, 59, 59, 999999)


def test_obtener_rango_dia_con_fecha_explicita_no_cambia(db, tenant_a):
    """Regresión: pasar una fecha explícita sigue funcionando exactamente
    igual que antes (ahora_utc no debe pisar una fecha ya dada)."""
    planta = _preparar_planta(db, tenant_a)
    context = TenantContext(tenant_id=tenant_a, sub_tenant_id=str(planta.id))
    ahora_utc = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)

    inicio, fin = obtener_rango_dia(date(2026, 8, 20), context, db, ahora_utc=ahora_utc)

    assert inicio == datetime(2026, 8, 20, 3, 0)
    assert fin.date() == date(2026, 8, 21)


def test_obtener_rango_dia_sin_contexto_cae_al_fallback_utc_puro(db):
    """Sin context/db (mismo camino de siempre para llamadas sin tenant
    resuelto), el comportamiento histórico se mantiene intacto."""
    inicio, fin = obtener_rango_dia(date(2026, 8, 18), None, None)
    assert inicio == datetime(2026, 8, 18, 0, 0)
    assert fin == datetime(2026, 8, 18, 23, 59, 59, 999999)


# ---------------------------------------------------------------------
# BE-P0-05: recomputar_eventos -- ya no pierde las últimas horas del día
# local (antes datetime.combine naive)
# ---------------------------------------------------------------------

def _preparar_escenario_estacion(db, tenant_id):
    planta = _preparar_planta(db, tenant_id)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea BE-P0-05")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación BE-P0-05", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def test_recomputar_eventos_encuentra_evento_de_la_noche_local_del_dia_pedido(client, db, tenant_a, gerente_a):
    """El caso concreto que el datetime.combine naive rompía: un evento a
    las 23:30 hora LOCAL de Buenos Aires del 18/8 se persiste como
    2026-08-19 02:30 UTC (mismo criterio que scans.py::
    _normalizar_timestamp_utc). Pedir recomputar_eventos para
    fecha_desde=fecha_hasta=2026-08-18 (el día LOCAL en que realmente
    ocurrió) tiene que encontrarlo -- el rango naive viejo
    ([2026-08-18 00:00, 23:59:59] UTC puro) lo dejaba afuera por 2h30."""
    _planta, _linea, estacion = _preparar_escenario_estacion(db, tenant_a)

    autenticar_como(gerente_a.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)})
    assert r.status_code == 201
    credencial = r.json()["credencial_completa"]

    # 23:30 LOCAL Buenos Aires de AYER (el endpoint rechaza timestamps a
    # más de 5 minutos en el futuro o más de 7 días de antigüedad contra
    # el reloj real -- "ayer" mantiene el test válido sin importar cuándo
    # se corra, a diferencia de una fecha fija). Offset explícito
    # (-03:00) para que _normalizar_timestamp_utc no lo reinterprete.
    ayer_local = datetime.now(timezone.utc).date() - timedelta(days=1)
    ts_local_con_offset = f"{ayer_local.isoformat()}T23:30:00-03:00"
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": ts_local_con_offset},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201

    from sqlmodel import select
    from app.models.domain import LiteEventoProduccion
    evento = db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id))).first()
    dia_utc_siguiente = ayer_local + timedelta(days=1)
    assert evento.timestamp == datetime(dia_utc_siguiente.year, dia_utc_siguiente.month, dia_utc_siguiente.day, 2, 30)

    autenticar_como(gerente_a.id)
    r = client.post(
        f"/config/estaciones/{estacion.id}/recomputar-eventos/",
        json={"fecha_desde": ayer_local.isoformat(), "fecha_hasta": ayer_local.isoformat()},
    )
    assert r.status_code == 200
    body = r.json()
    # Con el bug viejo (datetime.combine naive) esto daba 0 -- el evento
    # quedaba fuera del rango [2026-08-18 00:00, 23:59:59] UTC puro.
    assert body["eventos_recomputados"] + body["eventos_sin_cambios"] == 1

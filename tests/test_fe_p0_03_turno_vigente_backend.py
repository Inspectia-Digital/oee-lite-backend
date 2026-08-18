"""FE-P0-03 (PRD Go-Live Green Mills, sección 4): el BACKEND resuelve qué
turno está vigente, no la terminal.

Antes el kiosco lo calculaba con `now.getHours()` del propio dispositivo
(TerminalPage.tsx) -- una tablet de planta con el reloj mal configurado, o
en otro huso, fichaba al operario contra el turno equivocado sin que nada
lo señalara. Ahora GET /api/lite/estaciones/{id}/validar devuelve
`turno_vigente_id`, resuelto con la hora LOCAL DE LA PLANTA.

Criterio de aceptación del PRD: "Terminal con reloj desincronizado
(+/- 2hs) sigue resolviendo el turno correcto vía backend" -- lo que se
prueba acá es justamente eso: la respuesta no depende de ningún dato que
mande el cliente, sólo del reloj del servidor + Planta.timezone.
"""
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.domain import Estacion, Linea, Planta, Turno
from tests.conftest import autenticar_como

TZ_PLANTA = "America/Argentina/Buenos_Aires"
DIA_ISO_TODOS = "1,2,3,4,5,6,7"


def _preparar(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta FE-P0-03", timezone=TZ_PLANTA)
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea FE-P0-03")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Est FE-P0-03", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    return planta, linea, estacion


def _credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _validar(client, credencial, estacion_id):
    r = client.get(
        f"/api/lite/estaciones/{estacion_id}/validar",
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _hora_local_ahora() -> time:
    """Hora actual EN LA PLANTA -- para armar turnos que la cubran (o no)
    sin depender del huso en que corra el test."""
    return datetime.now(ZoneInfo(TZ_PLANTA)).time()


def test_devuelve_el_turno_que_cubre_la_hora_actual_de_planta(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar(db, tenant_a)
    credencial = _credencial(client, gerente_a, estacion.id)

    # Turno que cubre TODO el día: cualquiera sea la hora real, aplica.
    turno = Turno(
        tenant_id=tenant_a, nombre="Full", linea_id=linea.id,
        hora_inicio=time(0, 0), hora_fin=time(23, 59), dias_semana=DIA_ISO_TODOS,
    )
    db.add(turno)
    db.commit()
    db.refresh(turno)

    body = _validar(client, credencial, estacion.id)
    assert body["turno_vigente_id"] == str(turno.id)


def test_sin_turno_que_cubra_la_hora_actual_devuelve_none(client, db, tenant_a, gerente_a):
    """"Sin turno vigente" es una respuesta legítima -- la terminal pide
    selección manual en ese caso, no inventa un turno."""
    planta, linea, estacion = _preparar(db, tenant_a)
    credencial = _credencial(client, gerente_a, estacion.id)

    # Ventana de 1 hora que NO contiene la hora actual de planta.
    ahora = _hora_local_ahora()
    inicio_h = (ahora.hour + 3) % 24
    fin_h = (ahora.hour + 4) % 24
    db.add(Turno(
        tenant_id=tenant_a, nombre="Lejano", linea_id=linea.id,
        hora_inicio=time(inicio_h, 0), hora_fin=time(fin_h, 0), dias_semana=DIA_ISO_TODOS,
    ))
    db.commit()

    body = _validar(client, credencial, estacion.id)
    assert body["turno_vigente_id"] is None


def test_elige_el_turno_correcto_entre_varios_de_la_misma_linea(client, db, tenant_a, gerente_a):
    """El caso que el reloj del dispositivo podía arruinar: dos turnos
    contiguos, y hay que quedarse con el que realmente corre AHORA en la
    planta."""
    planta, linea, estacion = _preparar(db, tenant_a)
    credencial = _credencial(client, gerente_a, estacion.id)

    ahora = _hora_local_ahora()
    # Turno A: la hora anterior a ahora (no aplica).
    # Turno B: la ventana que contiene a ahora (aplica).
    inicio_a = (ahora.hour - 2) % 24
    inicio_b = ahora.hour
    turno_a = Turno(
        tenant_id=tenant_a, nombre="Anterior", linea_id=linea.id,
        hora_inicio=time(inicio_a, 0), hora_fin=time(inicio_a, 59), dias_semana=DIA_ISO_TODOS,
    )
    turno_b = Turno(
        tenant_id=tenant_a, nombre="Actual", linea_id=linea.id,
        hora_inicio=time(inicio_b, 0), hora_fin=time(inicio_b, 59), dias_semana=DIA_ISO_TODOS,
    )
    db.add_all([turno_a, turno_b])
    db.commit()
    db.refresh(turno_b)

    body = _validar(client, credencial, estacion.id)
    assert body["turno_vigente_id"] == str(turno_b.id)


def test_la_respuesta_no_depende_de_nada_que_mande_el_dispositivo(client, db, tenant_a, gerente_a):
    """Criterio literal del PRD: un dispositivo con el reloj corrido no
    puede cambiar el resultado. El endpoint no acepta NINGÚN parámetro de
    hora -- se verifica que dos llamadas idénticas dan lo mismo y que
    mandar cabeceras/parámetros de hora no altera nada."""
    planta, linea, estacion = _preparar(db, tenant_a)
    credencial = _credencial(client, gerente_a, estacion.id)
    turno = Turno(
        tenant_id=tenant_a, nombre="Full", linea_id=linea.id,
        hora_inicio=time(0, 0), hora_fin=time(23, 59), dias_semana=DIA_ISO_TODOS,
    )
    db.add(turno)
    db.commit()
    db.refresh(turno)

    normal = _validar(client, credencial, estacion.id)

    # Mismo request, pero simulando un dispositivo con el reloj +5h y
    # tratando de influir vía query params. Nada de esto debe cambiar la
    # resolución: el turno lo decide el servidor.
    desfasado = datetime.now(timezone.utc) + timedelta(hours=5)
    r = client.get(
        f"/api/lite/estaciones/{estacion.id}/validar",
        params={"ahora": desfasado.isoformat(), "hora": "23:59"},
        headers={"X-Device-Key": credencial, "X-Device-Time": desfasado.isoformat()},
    )
    assert r.status_code == 200
    assert r.json()["turno_vigente_id"] == normal["turno_vigente_id"] == str(turno.id)


def test_estacion_sin_turnos_configurados_devuelve_none(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar(db, tenant_a)
    credencial = _credencial(client, gerente_a, estacion.id)

    body = _validar(client, credencial, estacion.id)
    assert body["turnos"] == []
    assert body["turno_vigente_id"] is None

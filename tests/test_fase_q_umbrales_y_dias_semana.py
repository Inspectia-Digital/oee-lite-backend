"""Fase Q (feedback de producto sobre la app en uso):

- Turno.dias_semana: CRUD vía /config/turnos/.
- Umbrales heredables Estación > Línea > default de sistema, incluyendo
  el efecto real en /api/lite/scans (no sólo el CRUD de configuración).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import Estacion, Linea, LiteEventoProduccion, Planta, Turno
from tests.conftest import autenticar_como


def _preparar_planta_y_linea(db, tenant_id, **linea_kwargs):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Q")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Q", **linea_kwargs)
    db.add(linea)
    db.commit()
    db.refresh(linea)
    return planta, linea


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


# ---------- Turno.dias_semana ----------

def test_crear_turno_sin_dias_semana_default_todos_los_dias(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/config/turnos/", json={"nombre": "Full", "hora_inicio": "06:00:00", "hora_fin": "14:00:00"})
    assert r.status_code == 201
    assert r.json()["dias_semana"] == "1,2,3,4,5,6,7"


def test_crear_turno_con_dias_semana_lu_vi(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/turnos/",
        json={"nombre": "Lu-Vi", "hora_inicio": "06:00:00", "hora_fin": "14:00:00", "dias_semana": [1, 2, 3, 4, 5]},
    )
    assert r.status_code == 201
    assert r.json()["dias_semana"] == "1,2,3,4,5"


def test_crear_turno_dia_invalido_devuelve_422(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/turnos/",
        json={"nombre": "Malo", "hora_inicio": "06:00:00", "hora_fin": "14:00:00", "dias_semana": [0, 9]},
    )
    assert r.status_code == 422


def test_actualizar_turno_dias_semana(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/config/turnos/", json={"nombre": "T", "hora_inicio": "06:00:00", "hora_fin": "14:00:00"})
    turno_id = r.json()["id"]

    r = client.patch(f"/config/turnos/{turno_id}", json={"dias_semana": [6, 7]})
    assert r.status_code == 200
    assert r.json()["dias_semana"] == "6,7"


def test_actualizar_turno_sin_mandar_dias_semana_no_lo_toca(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/turnos/",
        json={"nombre": "T", "hora_inicio": "06:00:00", "hora_fin": "14:00:00", "dias_semana": [1, 3, 5]},
    )
    turno_id = r.json()["id"]

    r = client.patch(f"/config/turnos/{turno_id}", json={"nombre": "T Renombrado"})
    assert r.status_code == 200
    assert r.json()["dias_semana"] == "1,3,5"  # no se tocó


# ---------- Umbrales heredables Línea/Estación (CRUD) ----------

def test_crear_linea_con_umbrales(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta Q2")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Con umbrales", "planta_id": str(planta.id), "umbral_optimo": 100, "umbral_lento": 130, "umbral_alerta": 160},
    )
    assert r.status_code == 201
    assert r.json()["umbral_optimo"] == 100
    assert r.json()["umbral_lento"] == 130
    assert r.json()["umbral_alerta"] == 160


def test_crear_estacion_sin_umbrales_queda_null(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_planta_y_linea(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post("/config/estaciones/", json={"nombre": "Est", "tipo": "sensor", "linea_id": str(linea.id)})
    assert r.status_code == 201
    assert r.json()["umbral_optimo"] is None
    assert r.json()["umbral_lento"] is None
    assert r.json()["umbral_alerta"] is None


def test_estacion_puede_volver_a_heredar_seteando_null_por_patch(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_planta_y_linea(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/estaciones/",
        json={"nombre": "Est", "tipo": "sensor", "linea_id": str(linea.id), "umbral_optimo": 50},
    )
    estacion_id = r.json()["id"]
    assert r.json()["umbral_optimo"] == 50

    r = client.patch(f"/config/estaciones/{estacion_id}", json={"umbral_optimo": None})
    assert r.status_code == 200
    assert r.json()["umbral_optimo"] is None


# ---------- Efecto real en /api/lite/scans (resolución Estación > Línea > sistema) ----------

def test_scans_hereda_umbral_de_linea_cuando_estacion_no_lo_tiene(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_planta_y_linea(db, tenant_a, umbral_optimo=50, umbral_lento=60, umbral_alerta=70)
    autenticar_como(gerente_a.id)
    r = client.post("/config/estaciones/", json={"nombre": "Est", "tipo": "sensor", "linea_id": str(linea.id)})
    estacion_id = r.json()["id"]

    credencial = _emitir_credencial(client, gerente_a, estacion_id)
    ahora = datetime.now(timezone.utc)

    # Dos eventos con 65s de diferencia: entre el umbral_lento (60) y el
    # umbral_alerta (70) de la LÍNEA -> debería salir LENTO.
    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": estacion_id, "timestamp": ahora.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": estacion_id, "timestamp": (ahora + timedelta(seconds=65)).isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    evento2 = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == estacion_id).order_by(LiteEventoProduccion.timestamp.desc())
    ).first()
    assert evento2.estado == "LENTO"  # si no heredara de la línea, caería al default de sistema (280/300) y saldría OPTIMO


def test_scans_estacion_pisa_el_default_de_linea(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_planta_y_linea(db, tenant_a, umbral_optimo=50, umbral_lento=60, umbral_alerta=70)
    autenticar_como(gerente_a.id)
    # La estación tiene su PROPIO umbral_alerta, más alto que el de la línea.
    r = client.post(
        "/config/estaciones/",
        json={"nombre": "Est", "tipo": "sensor", "linea_id": str(linea.id), "umbral_optimo": 200, "umbral_lento": 250, "umbral_alerta": 300},
    )
    estacion_id = r.json()["id"]

    credencial = _emitir_credencial(client, gerente_a, estacion_id)
    ahora = datetime.now(timezone.utc)

    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": estacion_id, "timestamp": ahora.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    # 65s: por el umbral de LÍNEA sería ALERTA (>70), pero la ESTACIÓN
    # pisa con umbral_alerta=300 -> debería seguir OPTIMO.
    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": estacion_id, "timestamp": (ahora + timedelta(seconds=65)).isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    evento2 = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == estacion_id).order_by(LiteEventoProduccion.timestamp.desc())
    ).first()
    assert evento2.estado == "OPTIMO"


def test_scans_sin_linea_ni_estacion_configuradas_usa_default_de_sistema(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_planta_y_linea(db, tenant_a)  # sin umbrales
    autenticar_como(gerente_a.id)
    r = client.post("/config/estaciones/", json={"nombre": "Est", "tipo": "sensor", "linea_id": str(linea.id)})
    estacion_id = r.json()["id"]

    credencial = _emitir_credencial(client, gerente_a, estacion_id)
    ahora = datetime.now(timezone.utc)

    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": estacion_id, "timestamp": ahora.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    # 290s: entre el default de sistema umbral_lento(280) y umbral_alerta(300).
    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": estacion_id, "timestamp": (ahora + timedelta(seconds=290)).isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    evento2 = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == estacion_id).order_by(LiteEventoProduccion.timestamp.desc())
    ).first()
    assert evento2.estado == "LENTO"

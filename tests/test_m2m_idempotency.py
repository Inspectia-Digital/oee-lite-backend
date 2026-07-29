"""Fase D.1/D.4a (API keys M2M) y Fase E1 (idempotencia, timezone, máquina)."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import Estacion, Linea, Planta, LiteEventoProduccion, Maquina, MaquinaEstacion, Tenant
from tests.conftest import autenticar_como


def _crear_estacion_con_planta(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta M2M")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea M2M")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(
        tenant_id=tenant_id, nombre="Estación M2M", tipo="sensor", linea_id=linea.id,
        umbral_optimo=100, umbral_lento=150, umbral_alerta=200,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion


def _emitir_api_key(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"], r.json()["id"]


# ---------- API Keys M2M (D.1/D.4a) ----------

def test_maximo_dos_keys_activas_por_estacion(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    assert client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)}).status_code == 201
    assert client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)}).status_code == 201
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)})
    assert r.status_code == 409


def test_sin_credencial_devuelve_401(client, db, tenant_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    r = client.get(f"/api/lite/estaciones/{estacion.id}/validar")
    assert r.status_code == 401


def test_credencial_de_otra_estacion_devuelve_403(client, db, tenant_a, gerente_a):
    estacion_1 = _crear_estacion_con_planta(db, tenant_a)
    estacion_2 = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion_1.id)

    r = client.get(f"/api/lite/estaciones/{estacion_2.id}/validar", headers={"X-Device-Key": credencial})
    assert r.status_code == 403


def test_key_revocada_devuelve_403(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, key_db_id = _emitir_api_key(client, gerente_a, estacion.id)

    autenticar_como(gerente_a.id)
    assert client.post(f"/config/api-keys/{key_db_id}/revocar").status_code == 200

    r = client.get(f"/api/lite/estaciones/{estacion.id}/validar", headers={"X-Device-Key": credencial})
    assert r.status_code == 403


def test_suspension_total_corta_edge_incluso_con_key_valida(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion.id)

    t = db.get(Tenant, tenant_a)
    t.estado = "suspension_total"
    db.add(t)
    db.commit()

    r = client.get(f"/api/lite/estaciones/{estacion.id}/validar", headers={"X-Device-Key": credencial})
    assert r.status_code == 403


# ---------- Idempotencia y timezone (E1) ----------

def test_idempotencia_mismo_event_id_mismo_payload_devuelve_200(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion.id)
    event_id = str(uuid.uuid4())

    r1 = client.post("/api/lite/scans", json={"event_id": event_id, "id_estacion": str(estacion.id)}, headers={"X-Device-Key": credencial})
    assert r1.status_code == 201

    r2 = client.post("/api/lite/scans", json={"event_id": event_id, "id_estacion": str(estacion.id)}, headers={"X-Device-Key": credencial})
    assert r2.status_code == 200
    assert r2.json()["idempotente"] is True
    assert r2.json()["evento_id"] == r1.json()["evento_id"]

    total = len(db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.event_id == uuid.UUID(event_id))).all())
    assert total == 1


def test_idempotencia_mismo_event_id_payload_distinto_devuelve_409(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion.id)
    event_id = str(uuid.uuid4())

    client.post("/api/lite/scans", json={"event_id": event_id, "id_estacion": str(estacion.id)}, headers={"X-Device-Key": credencial})
    r = client.post("/api/lite/scans", json={"event_id": event_id, "id_estacion": str(estacion.id), "codigo_pieza": "distinto"}, headers={"X-Device-Key": credencial})
    assert r.status_code == 409


def test_timestamp_futuro_rechazado(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion.id)
    futuro = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()

    r = client.post("/api/lite/scans", json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": futuro}, headers={"X-Device-Key": credencial})
    assert r.status_code == 400


def test_timestamp_antiguo_rechazado(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion.id)
    viejo = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

    r = client.post("/api/lite/scans", json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": viejo}, headers={"X-Device-Key": credencial})
    assert r.status_code == 400


def test_maquina_no_asociada_se_acepta_con_null(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion.id)

    maquina = Maquina(tenant_id=tenant_a, codigo_externo="MAQ-SUELTA-TEST")
    db.add(maquina)
    db.commit()
    db.refresh(maquina)

    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "maquina_id": str(maquina.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    evento = db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.id == uuid.UUID(r.json()["evento_id"]))).first()
    assert evento.maquina_id is None


def test_estacion_inactiva_incluido_oee_false(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion.id)

    estacion.activa = False
    db.add(estacion)
    db.commit()

    r = client.post("/api/lite/scans", json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)}, headers={"X-Device-Key": credencial})
    assert r.status_code == 201
    evento = db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.id == uuid.UUID(r.json()["evento_id"]))).first()
    assert evento.incluido_oee is False


def test_unidades_rechazadas_no_puede_superar_procesadas(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial, _ = _emitir_api_key(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "unidades_rechazadas": 5},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 400

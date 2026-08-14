"""Fase CB (auditoría de robustez, batch 3): historial completo de
API-keys de dispositivo -- quién las emitió y cuándo se usaron por
última vez. Antes ApiKeyDispositivo no tenía ninguna de las dos
columnas y el panel (M2mSecurityPanel.tsx) sólo mostraba las activas,
sin ningún rastro de las revocadas."""
import time
import uuid
from datetime import datetime, timedelta

from sqlmodel import select

from app.core.auth_m2m import VENTANA_ULTIMO_USO
from app.models.domain import ApiKeyDispositivo, Estacion, Linea, Planta
from tests.conftest import autenticar_como


def _crear_estacion_con_planta(db, tenant_id, nombre="Estación CB"):
    planta = Planta(tenant_id=tenant_id, nombre=f"Planta {nombre}")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre=f"Línea {nombre}")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(
        tenant_id=tenant_id, nombre=nombre, tipo="sensor", linea_id=linea.id,
        umbral_optimo=100, umbral_lento=150, umbral_alerta=200,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion


def test_emitir_api_key_registra_el_creador(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Creador")
    gerente_a.nombre = "Ana"
    gerente_a.apellido = "Gerente"
    db.add(gerente_a)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)})
    assert r.status_code == 201
    body = r.json()
    assert body["creado_por_id"] == str(gerente_a.id)
    assert body["creado_por_nombre"] == "Ana Gerente"
    assert body["ultimo_uso_at"] is None


def test_listar_api_keys_incluye_creado_por_nombre(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Listado")
    gerente_a.nombre = "Bruno"
    gerente_a.apellido = "Supervisor"
    db.add(gerente_a)
    db.commit()

    autenticar_como(gerente_a.id)
    client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)})

    r = client.get(f"/config/api-keys/?estacion_id={estacion.id}")
    assert r.status_code == 200
    keys = r.json()
    assert len(keys) == 1
    assert keys[0]["creado_por_nombre"] == "Bruno Supervisor"


def test_listar_api_keys_incluye_revocadas_historial_completo(client, db, tenant_a, gerente_a):
    """Antes de esta fase el panel sólo mostraba activas -- confirma que
    el endpoint (que ya no filtraba por `activo`) sigue sin filtrar, y
    que una key revocada aparece con su `revoked_at`."""
    estacion = _crear_estacion_con_planta(db, tenant_a, "Revocada Historial")
    autenticar_como(gerente_a.id)
    creada = client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)}).json()

    r_revocar = client.post(f"/config/api-keys/{creada['id']}/revocar")
    assert r_revocar.status_code == 200

    r_listar = client.get(f"/config/api-keys/?estacion_id={estacion.id}")
    keys = r_listar.json()
    assert len(keys) == 1
    assert keys[0]["activo"] is False
    assert keys[0]["revoked_at"] is not None


def test_api_key_sin_creador_previo_no_rompe(client, db, tenant_a, gerente_a):
    """Keys emitidas antes de esta fase no tienen creado_por_id -- el
    endpoint tiene que devolver None, no explotar."""
    estacion = _crear_estacion_con_planta(db, tenant_a, "Legacy")
    key_vieja = ApiKeyDispositivo(
        tenant_id=tenant_a, key_id=f"legacy-{uuid.uuid4().hex[:8]}", secret_hash="x",
        estacion_id=estacion.id, activo=True, expires_at=datetime.utcnow() + timedelta(days=1),
    )
    db.add(key_vieja)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(f"/config/api-keys/?estacion_id={estacion.id}")
    assert r.status_code == 200
    body = r.json()[0]
    assert body["creado_por_id"] is None
    assert body["creado_por_nombre"] is None


def test_autenticar_dispositivo_actualiza_ultimo_uso(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "UltimoUso")
    autenticar_como(gerente_a.id)
    creada = client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)}).json()
    credencial = creada["credencial_completa"]

    r = client.get(
        f"/api/lite/estaciones/{estacion.id}/validar",
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 200

    fila = db.exec(select(ApiKeyDispositivo).where(ApiKeyDispositivo.id == uuid.UUID(creada["id"]))).first()
    assert fila.ultimo_uso_at is not None


def test_autenticar_dispositivo_no_reescribe_ultimo_uso_dentro_de_la_ventana(client, db, tenant_a, gerente_a):
    """Una terminal activa autentica un scan por pieza -- reescribir la
    fila en cada auth exitosa multiplicaría la carga de escritura sin
    necesidad real (ver VENTANA_ULTIMO_USO en auth_m2m.py)."""
    estacion = _crear_estacion_con_planta(db, tenant_a, "VentanaUso")
    autenticar_como(gerente_a.id)
    creada = client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)}).json()
    credencial = creada["credencial_completa"]
    api_key_id = uuid.UUID(creada["id"])

    client.get(f"/api/lite/estaciones/{estacion.id}/validar", headers={"X-Device-Key": credencial})
    primer_uso = db.exec(select(ApiKeyDispositivo).where(ApiKeyDispositivo.id == api_key_id)).first().ultimo_uso_at

    time.sleep(0.05)
    client.get(f"/api/lite/estaciones/{estacion.id}/validar", headers={"X-Device-Key": credencial})
    segundo_uso = db.exec(select(ApiKeyDispositivo).where(ApiKeyDispositivo.id == api_key_id)).first().ultimo_uso_at

    assert segundo_uso == primer_uso

    # Simula que la ventana venció -- el próximo auth sí debe actualizar.
    db.execute(
        ApiKeyDispositivo.__table__.update()
        .where(ApiKeyDispositivo.id == api_key_id)
        .values(ultimo_uso_at=datetime.utcnow() - VENTANA_ULTIMO_USO - timedelta(seconds=1))
    )
    db.commit()

    client.get(f"/api/lite/estaciones/{estacion.id}/validar", headers={"X-Device-Key": credencial})
    tercer_uso = db.exec(select(ApiKeyDispositivo).where(ApiKeyDispositivo.id == api_key_id)).first().ultimo_uso_at
    assert tercer_uso > segundo_uso

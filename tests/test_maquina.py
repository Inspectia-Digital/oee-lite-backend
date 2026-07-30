"""Fase G: CRUD de Máquina + asociación N:N con Estación."""
import uuid

from sqlmodel import select

from app.models.domain import Estacion, Linea, Maquina, Planta
from tests.conftest import autenticar_como


def _crear_linea_y_estacion(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta G")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea G")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación G", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion


def test_crear_y_listar_maquina(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/config/maquinas/", json={"codigo_externo": "PLC-01", "nombre": "PLC Línea 1"})
    assert r.status_code == 201
    assert r.json()["codigo_externo"] == "PLC-01"

    r = client.get("/config/maquinas/")
    assert r.status_code == 200
    assert any(m["codigo_externo"] == "PLC-01" for m in r.json())


def test_codigo_externo_duplicado_devuelve_409(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    payload = {"codigo_externo": "PLC-DUP", "nombre": "Uno"}
    assert client.post("/config/maquinas/", json=payload).status_code == 201
    assert client.post("/config/maquinas/", json=payload).status_code == 409


def test_baja_de_maquina_es_logica(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    creado = client.post("/config/maquinas/", json={"codigo_externo": "PLC-BAJA"}).json()

    r = client.delete(f"/config/maquinas/{creado['id']}")
    assert r.status_code == 200

    maquina_db = db.get(Maquina, uuid.UUID(creado["id"]))
    assert maquina_db is not None
    assert maquina_db.activo is False

    ids_normal = [m["id"] for m in client.get("/config/maquinas/").json()]
    assert creado["id"] not in ids_normal


def test_asociar_maquina_a_estacion_y_listar(client, db, tenant_a, gerente_a):
    estacion = _crear_linea_y_estacion(db, tenant_a)
    autenticar_como(gerente_a.id)
    maquina = client.post("/config/maquinas/", json={"codigo_externo": "PLC-ASOC"}).json()

    r = client.post(f"/config/maquinas/{maquina['id']}/estaciones", json={"estacion_id": str(estacion.id)})
    assert r.status_code == 201
    asociacion_id = r.json()["id"]

    r = client.get(f"/config/maquinas/{maquina['id']}/estaciones")
    assert r.status_code == 200
    assert any(a["id"] == asociacion_id for a in r.json())


def test_asociar_maquina_duplicada_devuelve_409(client, db, tenant_a, gerente_a):
    estacion = _crear_linea_y_estacion(db, tenant_a)
    autenticar_como(gerente_a.id)
    maquina = client.post("/config/maquinas/", json={"codigo_externo": "PLC-DUPASOC"}).json()

    payload = {"estacion_id": str(estacion.id)}
    assert client.post(f"/config/maquinas/{maquina['id']}/estaciones", json=payload).status_code == 201
    assert client.post(f"/config/maquinas/{maquina['id']}/estaciones", json=payload).status_code == 409


def test_quitar_asociacion_no_borra_fisicamente(client, db, tenant_a, gerente_a):
    estacion = _crear_linea_y_estacion(db, tenant_a)
    autenticar_como(gerente_a.id)
    maquina = client.post("/config/maquinas/", json={"codigo_externo": "PLC-QUITAR"}).json()
    asociacion = client.post(f"/config/maquinas/{maquina['id']}/estaciones", json={"estacion_id": str(estacion.id)}).json()

    r = client.delete(f"/config/maquinas/{maquina['id']}/estaciones/{asociacion['id']}")
    assert r.status_code == 200

    ids_activos = [a["id"] for a in client.get(f"/config/maquinas/{maquina['id']}/estaciones").json()]
    assert asociacion["id"] not in ids_activos


def test_maquina_de_otro_tenant_no_es_visible(client, db, tenant_a, tenant_b, gerente_a, gerente_b):
    autenticar_como(gerente_b.id)
    creado = client.post("/config/maquinas/", json={"codigo_externo": "PLC-TENANT-B"}).json()

    autenticar_como(gerente_a.id)
    r = client.get(f"/config/maquinas/{creado['id']}")
    assert r.status_code == 404

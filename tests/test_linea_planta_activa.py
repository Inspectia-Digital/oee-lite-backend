"""Fase M: crear línea sin planta_id en el body usa la planta activa
(X-Sub-Tenant-Id) -- el front ya la trackea vía el switcher y no la manda
en el payload de creación."""
from app.models.domain import Planta
from tests.conftest import autenticar_como


def test_crear_linea_usa_planta_activa_del_header(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="San Fernando")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Panes", "modo_asignacion_operarios": "manual", "activa": True},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201
    assert r.json()["planta_id"] == str(planta.id)


def test_crear_linea_sin_planta_id_ni_header_devuelve_400(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/config/lineas/", json={"nombre": "Panes"})
    assert r.status_code == 400


def test_crear_linea_planta_id_explicito_tiene_prioridad_sobre_header(client, db, tenant_a, gerente_a):
    planta_activa = Planta(tenant_id=tenant_a, nombre="Planta Activa")
    planta_explicita = Planta(tenant_id=tenant_a, nombre="Planta Explicita")
    db.add(planta_activa)
    db.add(planta_explicita)
    db.commit()
    db.refresh(planta_activa)
    db.refresh(planta_explicita)

    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Panes", "planta_id": str(planta_explicita.id)},
        headers={"X-Sub-Tenant-Id": str(planta_activa.id)},
    )
    assert r.status_code == 201
    assert r.json()["planta_id"] == str(planta_explicita.id)

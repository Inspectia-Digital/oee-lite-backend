"""Fase DP (auditoría de backend, P1-03): Planta.timezone se valida
contra la base IANA en creación y edición -- antes se persistía
cualquier string sin chequear, un typo rompía en silencio cualquier
cálculo de fecha/turno que dependiera de esa planta."""
from app.models.domain import Planta
from tests.conftest import autenticar_como


def test_crear_planta_timezone_valido_devuelve_201(client, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(
        "/accesos/mi-empresa/sub-tenants",
        json={"nombre": "San Fernando", "timezone": "America/Buenos_Aires"},
    )
    assert r.status_code == 201
    assert r.json()["timezone"] == "America/Buenos_Aires"


def test_crear_planta_timezone_invalido_devuelve_422(client, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(
        "/accesos/mi-empresa/sub-tenants",
        json={"nombre": "San Fernando", "timezone": "Norteamerica/Mexico"},
    )
    assert r.status_code == 422


def test_editar_planta_timezone_valido_devuelve_200(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Morón", timezone="America/Buenos_Aires")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    r = client.patch(
        f"/accesos/mi-empresa/sub-tenants/{planta.id}",
        json={"timezone": "Europe/Madrid"},
    )
    assert r.status_code == 200
    assert r.json()["timezone"] == "Europe/Madrid"


def test_editar_planta_timezone_invalido_devuelve_422(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Morón", timezone="America/Buenos_Aires")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    r = client.patch(
        f"/accesos/mi-empresa/sub-tenants/{planta.id}",
        json={"timezone": "Invalid/Zone"},
    )
    assert r.status_code == 422

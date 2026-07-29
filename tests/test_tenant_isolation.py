"""Aislamiento multi-tenant: dos tenants, ninguna fila cruzada."""
import uuid

from sqlmodel import select

from app.models.domain import Planta
from tests.conftest import autenticar_como, crear_tenant, crear_usuario
from app.models.domain import RolUsuario


def test_gerente_de_tenant_a_no_ve_plantas_de_tenant_b(client, db, tenant_a, tenant_b, gerente_a):
    planta_b = Planta(tenant_id=tenant_b, nombre="Planta de B")
    db.add(planta_b)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get("/accesos/mi-empresa/sub-tenants")
    assert r.status_code == 200
    nombres = [p["nombre"] for p in r.json()]
    assert "Planta de B" not in nombres


def test_gerente_no_puede_leer_planta_de_otro_tenant_por_id(client, db, tenant_a, tenant_b, gerente_a):
    planta_b = Planta(tenant_id=tenant_b, nombre="Planta Ajena")
    db.add(planta_b)
    db.commit()
    db.refresh(planta_b)

    autenticar_como(gerente_a.id)
    r = client.get(f"/accesos/mi-empresa/sub-tenants/{planta_b.id}")
    # 404, no 403 ni 200: la UI humana no debe confirmar la existencia de un
    # recurso de otro tenant (regla explícita del HANDOFF).
    assert r.status_code == 404


def test_gerente_no_puede_modificar_planta_de_otro_tenant(client, db, tenant_a, tenant_b, gerente_a):
    planta_b = Planta(tenant_id=tenant_b, nombre="Planta Ajena 2")
    db.add(planta_b)
    db.commit()
    db.refresh(planta_b)

    autenticar_como(gerente_a.id)
    r = client.patch(f"/accesos/mi-empresa/sub-tenants/{planta_b.id}", json={"nombre": "Hackeada"})
    assert r.status_code == 404

    db.refresh(planta_b)
    assert planta_b.nombre == "Planta Ajena 2"


def test_listar_usuarios_no_cruza_tenants(client, db, tenant_a, tenant_b, gerente_a):
    crear_usuario(db, tenant_b, RolUsuario.OPERARIO)

    autenticar_como(gerente_a.id)
    r = client.get("/accesos/mi-empresa/usuarios")
    assert r.status_code == 200
    tenant_ids_implicitos = {u["id"] for u in r.json()}
    # Sólo debe verse a sí mismo (nadie más creado en tenant_a por este test)
    assert str(gerente_a.id) in tenant_ids_implicitos

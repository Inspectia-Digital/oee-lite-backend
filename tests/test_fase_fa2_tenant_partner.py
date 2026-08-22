"""Fase FA.2 (PRD Demo/Partners/Marketplace/Soporte/Planes): Tenant tipo
Partner/Canal/Consultor. Cubre el alta de un partner (reusa POST
/accesos/superadmin/tenants con categoria='partner', no un endpoint
nuevo), el CRUD global de material comercial (SuperAdmin exclusivo) y
que sólo tenants categoria=partner puedan leerlo -- un tenant cliente
normal no ve la sección."""
import uuid

from sqlmodel import select

from app.models.domain import Tenant
from tests.conftest import autenticar_como


def _id_unico(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def test_crear_tenant_partner_via_endpoint_existente(client, superadmin):
    autenticar_como(superadmin.id)
    tenant_id = _id_unico("partner")
    r = client.post("/accesos/superadmin/tenants", json={
        "id": tenant_id, "nombre": "Partner de Prueba SA", "categoria": "partner",
    })
    assert r.status_code == 200, r.text
    assert r.json()["tenant"]["categoria"] == "partner"


def test_crear_tenant_sin_categoria_default_cliente(client, superadmin):
    autenticar_como(superadmin.id)
    tenant_id = _id_unico("cliente")
    r = client.post("/accesos/superadmin/tenants", json={"id": tenant_id, "nombre": "Cliente Normal SA"})
    assert r.status_code == 200
    assert r.json()["tenant"]["categoria"] == "cliente"


def test_no_superadmin_no_puede_crud_materiales(client, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/admin/materiales-partner", json={"titulo": "Deck de ventas"})
    assert r.status_code == 403


def test_superadmin_crea_lista_actualiza_y_elimina_material(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/admin/materiales-partner", json={
        "titulo": "Deck de ventas", "descripcion": "Presentación general", "categoria": "presentacion",
    })
    assert r.status_code == 201, r.text
    material = r.json()
    assert material["titulo"] == "Deck de ventas"
    assert material["visible"] is True

    r = client.get("/admin/materiales-partner")
    assert r.status_code == 200
    assert any(m["id"] == material["id"] for m in r.json())

    r = client.put(f"/admin/materiales-partner/{material['id']}", json={"visible": False})
    assert r.status_code == 200
    assert r.json()["visible"] is False

    r = client.delete(f"/admin/materiales-partner/{material['id']}")
    assert r.status_code == 204

    r = client.get("/admin/materiales-partner")
    assert not any(m["id"] == material["id"] for m in r.json())


def test_tenant_partner_ve_solo_materiales_visibles(client, db, superadmin, tenant_a, gerente_a):
    tenant_db = db.exec(select(Tenant).where(Tenant.id == tenant_a)).first()
    tenant_db.categoria = "partner"
    db.add(tenant_db)
    db.commit()

    autenticar_como(superadmin.id)
    client.post("/admin/materiales-partner", json={"titulo": "Visible", "visible": True})
    client.post("/admin/materiales-partner", json={"titulo": "Oculto", "visible": False})

    autenticar_como(gerente_a.id)
    r = client.get("/mi-empresa/materiales-partner")
    assert r.status_code == 200
    titulos = [m["titulo"] for m in r.json()]
    assert "Visible" in titulos
    assert "Oculto" not in titulos


def test_tenant_cliente_no_puede_ver_materiales_partner(client, tenant_a, gerente_a):
    """tenant_a por default es categoria='cliente' -- sin el fixture
    anterior mutándolo a partner, esta sección queda bloqueada."""
    autenticar_como(gerente_a.id)
    r = client.get("/mi-empresa/materiales-partner")
    assert r.status_code == 403

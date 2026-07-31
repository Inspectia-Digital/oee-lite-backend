"""Fase M: endpoint dedicado para que SuperAdmin edite los módulos
contratados de un tenant (gap real: TenantUpdate no exponía este campo,
no había forma de sacarle 'oee-hub' a un tenant que no debía tenerlo)."""
from app.models.domain import Tenant
from tests.conftest import autenticar_como


def test_superadmin_puede_actualizar_modulos(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}/modulos", json={"modulos_contratados": ["tymeo"]})
    assert r.status_code == 200

    tenant_db = db.get(Tenant, tenant_a)
    assert tenant_db.modulos_contratados == "tymeo"


def test_quitar_oee_hub_de_un_tenant(client, db, tenant_a, superadmin):
    t = db.get(Tenant, tenant_a)
    t.modulos_contratados = "tymeo,oee-hub"
    db.add(t)
    db.commit()

    autenticar_como(superadmin.id)
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}/modulos", json={"modulos_contratados": ["tymeo"]})
    assert r.status_code == 200

    tenant_db = db.get(Tenant, tenant_a)
    assert "oee-hub" not in tenant_db.modulos_contratados.split(",")


def test_gerencia_no_puede_llamar_al_endpoint_de_modulos(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}/modulos", json={"modulos_contratados": ["tymeo"]})
    assert r.status_code == 403


def test_modulo_invalido_es_rechazado(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}/modulos", json={"modulos_contratados": ["no-existe"]})
    assert r.status_code == 422


def test_gerencia_no_puede_autoasignarse_modulos_via_patch_generico(client, db, tenant_a, gerente_a):
    """TenantUpdate (usado por PATCH /mi-empresa/tenant) no tiene el campo
    modulos_contratados -- mandarlo no debe tener ningún efecto."""
    autenticar_como(gerente_a.id)
    antes = db.get(Tenant, tenant_a).modulos_contratados
    r = client.patch("/accesos/mi-empresa/tenant", json={"nombre": "Nombre sin cambiar módulos", "modulos_contratados": "tymeo,oee-hub"})
    assert r.status_code == 200
    despues = db.get(Tenant, tenant_a).modulos_contratados
    assert despues == antes

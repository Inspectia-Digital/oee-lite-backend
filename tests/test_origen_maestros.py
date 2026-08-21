"""Reorg carga de SKUs por front (pedido Green Mills): origen_maestros ya
existía en el modelo de Tenant y ya estaba enforced en el backend para el
alta masiva por archivo (verificar_permiso_carga_y_linea, importaciones.py),
pero nunca tuvo forma de editarse por API -- no estaba en TenantUpdate.
Gap real, no un campo nuevo (ver también test_sku_manual.py para el guard
que faltaba en el alta individual).

Fase EZ.2 (pedido del usuario): origen_maestros gobernaba SKUs, Planes y
Órdenes con un solo toggle tenant-wide -- se dividió en dos campos
independientes: origen_maestros (SKUs) y origen_maestros_planes (Planes
y Órdenes). Ver test_crear_plan_bloqueado_si_origen_erp/
test_crear_orden_bloqueada_si_origen_erp en test_fase_aa_plan_produccion.py
para el guard del lado de Planes/Órdenes, y test_sku_manual.py para el de
SKUs -- acá sólo la independencia entre ambos campos."""
from tests.conftest import autenticar_como


def test_tenant_trae_origen_maestros_default_manual(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/accesos/mi-empresa/tenant")
    assert r.status_code == 200
    assert r.json()["origen_maestros"] == "MANUAL"


def test_gerencia_actualiza_origen_maestros(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.patch("/accesos/mi-empresa/tenant", json={"origen_maestros": "ERP"})
    assert r.status_code == 200
    assert r.json()["tenant"]["origen_maestros"] == "ERP"

    r2 = client.get("/accesos/mi-empresa/tenant")
    assert r2.status_code == 200
    assert r2.json()["origen_maestros"] == "ERP"


def test_actualizar_origen_maestros_no_afecta_otros_campos_de_tenant(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r_antes = client.get("/accesos/mi-empresa/tenant")
    nombre_original = r_antes.json()["nombre"]

    r = client.patch("/accesos/mi-empresa/tenant", json={"origen_maestros": "ERP"})
    assert r.status_code == 200
    assert r.json()["tenant"]["nombre"] == nombre_original


# ---------- Fase EZ.2: split SKUs vs Planes/Órdenes ----------

def test_tenant_trae_origen_maestros_planes_default_manual(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/accesos/mi-empresa/tenant")
    assert r.status_code == 200
    assert r.json()["origen_maestros_planes"] == "MANUAL"


def test_cambiar_origen_maestros_de_skus_no_toca_el_de_planes(client, db, tenant_a, gerente_a):
    """El bug real que motivó el split: antes un solo campo gobernaba
    los tres -- pasar SKUs a ERP no debe bloquear (ni afectar de ningún
    modo) la carga manual de Planes/Órdenes."""
    autenticar_como(gerente_a.id)
    r = client.patch("/accesos/mi-empresa/tenant", json={"origen_maestros": "ERP"})
    assert r.status_code == 200
    assert r.json()["tenant"]["origen_maestros"] == "ERP"
    assert r.json()["tenant"]["origen_maestros_planes"] == "MANUAL"


def test_cambiar_origen_maestros_de_planes_no_toca_el_de_skus(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.patch("/accesos/mi-empresa/tenant", json={"origen_maestros_planes": "ERP"})
    assert r.status_code == 200
    assert r.json()["tenant"]["origen_maestros_planes"] == "ERP"
    assert r.json()["tenant"]["origen_maestros"] == "MANUAL"

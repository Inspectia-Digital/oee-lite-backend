"""Fase FA.2.1: asignar la categoría (cliente/partner/interno) a un
tenant que YA existe.

Hueco real encontrado por el usuario: FA.2 agregó `Tenant.categoria`,
los endpoints de material comercial y el gating del sidebar, y permitía
pasar `categoria` al CREAR un tenant -- pero no al actualizarlo. O sea
que el caso normal (convertir un cliente que ya opera en Partner, como
BPS) era imposible por cualquier vía, ni siquiera por API. La
funcionalidad existía y era inalcanzable.

Incluye también el otro medio-bug del mismo hueco: el listado de
tenants no devolvía `categoria`, así que el panel mostraba "cliente"
para todos -- un dato incorrecto con apariencia de correcto."""
import uuid

from sqlmodel import select

from app.models.domain import Tenant
from tests.conftest import autenticar_como


def _id_unico(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def test_convertir_tenant_existente_en_partner(client, db, superadmin):
    """REGRESIÓN del hueco: antes no había forma de hacer esto."""
    autenticar_como(superadmin.id)
    tenant_id = _id_unico("cliente")
    client.post("/accesos/superadmin/tenants", json={"id": tenant_id, "nombre": "Cliente Que Sera Partner"})

    r = client.patch(f"/accesos/superadmin/tenants/{tenant_id}", json={"categoria": "partner"})
    assert r.status_code == 200, r.text
    assert r.json()["categoria"] == "partner"

    db.expire_all()
    assert db.get(Tenant, tenant_id).categoria == "partner"


def test_volver_a_cliente(client, db, superadmin):
    autenticar_como(superadmin.id)
    tenant_id = _id_unico("partner")
    client.post("/accesos/superadmin/tenants", json={
        "id": tenant_id, "nombre": "Partner", "categoria": "partner",
    })

    r = client.patch(f"/accesos/superadmin/tenants/{tenant_id}", json={"categoria": "cliente"})
    assert r.status_code == 200
    assert r.json()["categoria"] == "cliente"


def test_categoria_invalida_es_rechazada(client, superadmin):
    autenticar_como(superadmin.id)
    tenant_id = _id_unico("t")
    client.post("/accesos/superadmin/tenants", json={"id": tenant_id, "nombre": "T"})

    r = client.patch(f"/accesos/superadmin/tenants/{tenant_id}", json={"categoria": "revendedor"})
    assert r.status_code == 422


def test_categoria_invalida_al_crear_tambien_es_rechazada(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/accesos/superadmin/tenants", json={
        "id": _id_unico("t"), "nombre": "T", "categoria": "loquesea",
    })
    assert r.status_code == 422


def test_editar_otro_campo_no_pisa_la_categoria(client, db, superadmin):
    """Cambiar el nombre no debe devolver el tenant a "cliente" -- el
    caso que rompería si el endpoint escribiera el campo siempre en vez
    de sólo cuando viene (exclude_unset)."""
    autenticar_como(superadmin.id)
    tenant_id = _id_unico("partner")
    client.post("/accesos/superadmin/tenants", json={
        "id": tenant_id, "nombre": "Partner", "categoria": "partner",
    })

    r = client.patch(f"/accesos/superadmin/tenants/{tenant_id}", json={"nombre": "Partner Renombrado"})
    assert r.status_code == 200
    assert r.json()["nombre"] == "Partner Renombrado"
    assert r.json()["categoria"] == "partner"


def test_listado_de_tenants_devuelve_la_categoria(client, superadmin):
    """Sin esto el panel mostraba "cliente" para todos -- peor que no
    mostrar nada, porque parece un dato correcto."""
    autenticar_como(superadmin.id)
    tenant_id = _id_unico("partner")
    client.post("/accesos/superadmin/tenants", json={
        "id": tenant_id, "nombre": "Partner", "categoria": "partner",
    })

    r = client.get("/accesos/superadmin/tenants")
    assert r.status_code == 200
    fila = next((t for t in r.json() if t["id"] == tenant_id), None)
    assert fila is not None
    assert fila["categoria"] == "partner"


def test_demo_asociado_debe_apuntar_a_una_demo_real(client, db, superadmin, tenant_a):
    """Apuntar la demo de un Partner a un cliente real le daría acceso a
    datos productivos ajenos -- se valida en el backend, no sólo en la UI."""
    autenticar_como(superadmin.id)
    tenant_id = _id_unico("partner")
    client.post("/accesos/superadmin/tenants", json={
        "id": tenant_id, "nombre": "Partner", "categoria": "partner",
    })

    # tenant_a es un tenant normal (es_demo=False), no una demo.
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_id}", json={"demo_asociado_id": tenant_a})
    assert r.status_code == 422


def test_asociar_una_demo_real_funciona(client, db, superadmin):
    autenticar_como(superadmin.id)
    demo_id = client.post("/admin/demo/crear", json={"nombre": "Demo Partner", "industria": "textil"}).json()["id"]

    tenant_id = _id_unico("partner")
    client.post("/accesos/superadmin/tenants", json={
        "id": tenant_id, "nombre": "Partner", "categoria": "partner",
    })

    r = client.patch(f"/accesos/superadmin/tenants/{tenant_id}", json={
        "categoria": "partner", "demo_asociado_id": demo_id,
    })
    assert r.status_code == 200, r.text
    assert r.json()["demo_asociado_id"] == demo_id


def test_desasociar_la_demo(client, db, superadmin):
    """None explícito limpia el vínculo (el front lo manda al sacar al
    tenant de partner)."""
    autenticar_como(superadmin.id)
    demo_id = client.post("/admin/demo/crear", json={"nombre": "Demo", "industria": "textil"}).json()["id"]
    tenant_id = _id_unico("partner")
    client.post("/accesos/superadmin/tenants", json={
        "id": tenant_id, "nombre": "P", "categoria": "partner", "demo_asociado_id": demo_id,
    })

    r = client.patch(f"/accesos/superadmin/tenants/{tenant_id}", json={"demo_asociado_id": None})
    assert r.status_code == 200
    assert r.json()["demo_asociado_id"] is None


def test_convertir_en_partner_habilita_material_comercial(client, db, superadmin, gerente_a, tenant_a):
    """El efecto de punta a punta: la sección que FA.2 dejó construida
    pero inalcanzable ahora se puede habilitar de verdad."""
    autenticar_como(gerente_a.id)
    assert client.get("/mi-empresa/materiales-partner").status_code == 403

    autenticar_como(superadmin.id)
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}", json={"categoria": "partner"})
    assert r.status_code == 200

    autenticar_como(gerente_a.id)
    assert client.get("/mi-empresa/materiales-partner").status_code == 200

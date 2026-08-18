"""Fase EB (PRD "Billing MVP" v2.0): catálogo de módulos/planes de
precio/métodos de pago -- CRUD exclusivo SuperAdmin, y la migración real
de MODULOS_VALIDOS (admin.py) a ModuloDisponible (confirmado con el
usuario: "Migrar MODULE_CATALOG a la tabla nueva")."""
import uuid

from tests.conftest import autenticar_como


def _codigo(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


# ==========================================
# MÓDULOS DISPONIBLES
# ==========================================
def test_no_superadmin_no_puede_crear_modulo(client, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "X"})
    assert r.status_code == 403


def test_superadmin_crea_lista_actualiza_y_elimina_modulo(client, superadmin):
    autenticar_como(superadmin.id)
    codigo = _codigo("mod")

    r = client.post("/billing/modulos", json={
        "codigo": codigo, "nombre": "Módulo Test", "descripcion": "desc", "orden": 9, "estado": "beta",
    })
    assert r.status_code == 201, r.text
    modulo = r.json()
    assert modulo["codigo"] == codigo
    assert modulo["estado"] == "beta"

    r = client.get("/billing/modulos")
    assert r.status_code == 200
    assert any(m["codigo"] == codigo for m in r.json())

    r = client.put(f"/billing/modulos/{modulo['id']}", json={"estado": "activo", "orden": 1})
    assert r.status_code == 200
    assert r.json()["estado"] == "activo"
    assert r.json()["orden"] == 1

    r = client.delete(f"/billing/modulos/{modulo['id']}")
    assert r.status_code == 204

    r = client.get("/billing/modulos")
    assert not any(m["codigo"] == codigo for m in r.json())


def test_codigo_de_modulo_duplicado_devuelve_409(client, superadmin):
    autenticar_como(superadmin.id)
    codigo = _codigo("mod")
    r1 = client.post("/billing/modulos", json={"codigo": codigo, "nombre": "A"})
    assert r1.status_code == 201
    r2 = client.post("/billing/modulos", json={"codigo": codigo, "nombre": "B"})
    assert r2.status_code == 409


def test_no_se_puede_eliminar_modulo_con_planes(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "Con planes"})
    modulo_id = r.json()["id"]
    client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "free", "nombre": "Free", "precio": "0.00",
    })

    r = client.delete(f"/billing/modulos/{modulo_id}")
    assert r.status_code == 409


# ==========================================
# PLANES DE PRECIO
# ==========================================
def test_crea_lista_actualiza_y_elimina_plan_de_precio(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"})
    modulo_id = r.json()["id"]

    r = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": "500.00",
        "limite_usuarios": 15,
    })
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["precio"] == "500.00"
    assert plan["limite_usuarios"] == 15
    assert plan["limite_plantas"] is None  # NULL = ilimitado, por diseño (PRD)

    r = client.get(f"/billing/modulos/{modulo_id}/planes")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.put(f"/billing/planes/{plan['id']}", json={"precio": "450.00"})
    assert r.status_code == 200
    assert r.json()["precio"] == "450.00"

    r = client.delete(f"/billing/planes/{plan['id']}")
    assert r.status_code == 204


def test_precio_negativo_es_rechazado(client, superadmin):
    """PRD: "Validación: Precio debe ser >= 0"."""
    autenticar_como(superadmin.id)
    r = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"})
    modulo_id = r.json()["id"]

    r = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "malo", "nombre": "Malo", "precio": "-10.00",
    })
    assert r.status_code == 422


def test_plan_free_con_precio_cero_es_valido(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"})
    modulo_id = r.json()["id"]

    r = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "free", "nombre": "Free", "precio": "0.00",
    })
    assert r.status_code == 201
    assert r.json()["precio"] == "0.00"


def test_codigo_de_plan_duplicado_dentro_del_mismo_modulo_devuelve_409(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"})
    modulo_id = r.json()["id"]
    payload = {"modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": "100.00"}
    assert client.post(f"/billing/modulos/{modulo_id}/planes", json=payload).status_code == 201
    assert client.post(f"/billing/modulos/{modulo_id}/planes", json=payload).status_code == 409


def test_mismo_codigo_de_plan_en_modulos_distintos_no_choca(client, superadmin):
    """codigo es único DENTRO del módulo, no globalmente -- decisión de
    esta fase (el PRD no lo especifica con esa precisión)."""
    autenticar_como(superadmin.id)
    r1 = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M1"})
    r2 = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M2"})
    m1, m2 = r1.json()["id"], r2.json()["id"]

    r_a = client.post(f"/billing/modulos/{m1}/planes", json={
        "modulo_id": m1, "codigo": "pro", "nombre": "Pro", "precio": "100.00",
    })
    r_b = client.post(f"/billing/modulos/{m2}/planes", json={
        "modulo_id": m2, "codigo": "pro", "nombre": "Pro", "precio": "200.00",
    })
    assert r_a.status_code == 201
    assert r_b.status_code == 201


# ==========================================
# MÉTODOS DE PAGO
# ==========================================
def test_crea_lista_actualiza_y_elimina_metodo_de_pago(client, superadmin):
    autenticar_como(superadmin.id)
    codigo = _codigo("mp")

    r = client.post("/billing/metodos-pago", json={
        "codigo": codigo, "nombre": "Transferencia bancaria", "tipo": "transferencia",
        "detalle": "Banco X, cuenta 123", "orden": 1,
    })
    assert r.status_code == 201, r.text
    metodo = r.json()

    r = client.put(f"/billing/metodos-pago/{metodo['id']}", json={"estado": "inactivo"})
    assert r.status_code == 200
    assert r.json()["estado"] == "inactivo"

    r = client.delete(f"/billing/metodos-pago/{metodo['id']}")
    assert r.status_code == 204


# ==========================================
# Migración real: actualizar_modulos_tenant valida contra ModuloDisponible
# ==========================================
def test_asignar_modulo_real_al_tenant_funciona(client, superadmin, tenant_a):
    """Confirma que la migración de MODULOS_VALIDOS (constante
    hardcodeada, retirada) a ModuloDisponible (tabla real) sigue
    aceptando un código de módulo que sí existe en el catálogo -- creado
    acá mismo, sin depender de que seed.py ya haya corrido contra esta
    base (test autocontenido)."""
    autenticar_como(superadmin.id)
    codigo = _codigo("mod")
    r_modulo = client.post("/billing/modulos", json={"codigo": codigo, "nombre": "Real"})
    assert r_modulo.status_code == 201

    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}/modulos", json={
        "modulos_contratados": [codigo],
    })
    assert r.status_code == 200, r.text


def test_asignar_modulo_inexistente_al_tenant_devuelve_422(client, superadmin, tenant_a):
    """Antes esto se validaba contra MODULOS_VALIDOS (constante
    hardcodeada); ahora contra ModuloDisponible real -- un código que no
    existe en la tabla sigue rechazándose."""
    autenticar_como(superadmin.id)
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}/modulos", json={
        "modulos_contratados": ["modulo-que-no-existe"],
    })
    assert r.status_code == 422

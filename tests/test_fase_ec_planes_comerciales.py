"""Fase EC (PRD "Billing MVP" v2.0): planes_comerciales (descuentos/
bonificación) + asignación real de módulo+plan+descuento a un tenant
(tenant_modulos_asignados) -- CRUD exclusivo SuperAdmin, sobre las tablas
globales de Fase EB (ModuloDisponible/PlanPrecio/MetodoPagoConfigurado)."""
from datetime import date
from decimal import Decimal
import uuid

from tests.conftest import autenticar_como


def _codigo(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def _crear_modulo_y_plan(client, precio: str = "500.00"):
    """Helper: crea un módulo real + un plan de precio real (Fase EB),
    autocontenido, sin depender de seed.py."""
    r_modulo = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"})
    assert r_modulo.status_code == 201
    modulo_id = r_modulo.json()["id"]
    r_plan = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": precio,
    })
    assert r_plan.status_code == 201
    return modulo_id, r_plan.json()["id"]


def _crear_metodo_pago(client) -> str:
    r = client.post("/billing/metodos-pago", json={
        "codigo": _codigo("mp"), "nombre": "Transferencia", "tipo": "transferencia",
    })
    assert r.status_code == 201
    return r.json()["id"]


# ==========================================
# PLANES COMERCIALES: reglas de descuento
# ==========================================
def test_no_superadmin_no_puede_crear_plan_comercial(client, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "X", "descuento_porcentaje": "10.00",
        "fecha_inicio": "2026-01-01",
    })
    assert r.status_code == 403


def test_crea_plan_comercial_con_descuento_porcentaje(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Descuento 20%", "descuento_porcentaje": "20.00",
        "fecha_inicio": "2026-01-01",
    })
    assert r.status_code == 201, r.text
    plan_comercial = r.json()
    assert plan_comercial["es_bonificado"] is False
    assert plan_comercial["descuento_porcentaje"] == "20.00"
    assert plan_comercial["aplica_a_todos_modulos"] is True
    assert plan_comercial["aplica_a_todos_planes"] is True


def test_crea_plan_comercial_bonificado_meses_limitados(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "3 meses gratis", "es_bonificado": True,
        "meses_bonificados": 3, "fecha_inicio": "2026-01-01",
    })
    assert r.status_code == 201, r.text
    assert r.json()["meses_bonificados"] == 3
    assert r.json()["descuento_porcentaje"] is None


def test_crea_plan_comercial_bonificado_ilimitado(client, superadmin):
    """PRD §8: "100% bonificado ilimitado = deuda $0, estado 'al día'
    siempre" -- meses_bonificados=None es el caso ilimitado."""
    autenticar_como(superadmin.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Cortesía ilimitada", "es_bonificado": True,
        "fecha_inicio": "2026-01-01",
    })
    assert r.status_code == 201, r.text
    assert r.json()["meses_bonificados"] is None


def test_no_permite_bonificado_y_descuento_a_la_vez(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Inválido", "es_bonificado": True,
        "descuento_porcentaje": "10.00", "fecha_inicio": "2026-01-01",
    })
    assert r.status_code == 422


def test_no_permite_ni_bonificado_ni_descuento(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Inválido", "fecha_inicio": "2026-01-01",
    })
    assert r.status_code == 422


def test_descuento_porcentaje_fuera_de_rango_es_rechazado(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Inválido", "descuento_porcentaje": "150.00",
        "fecha_inicio": "2026-01-01",
    })
    assert r.status_code == 422


def test_lista_actualiza_y_elimina_plan_comercial(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Original", "descuento_porcentaje": "15.00",
        "fecha_inicio": "2026-01-01",
    })
    plan_comercial_id = r.json()["id"]

    r = client.get("/billing/planes-comerciales")
    assert r.status_code == 200
    assert any(p["id"] == plan_comercial_id for p in r.json())

    r = client.put(f"/billing/planes-comerciales/{plan_comercial_id}", json={"descuento_porcentaje": "25.00"})
    assert r.status_code == 200
    assert r.json()["descuento_porcentaje"] == "25.00"

    r = client.delete(f"/billing/planes-comerciales/{plan_comercial_id}")
    assert r.status_code == 204


def test_codigo_de_plan_comercial_duplicado_devuelve_409(client, superadmin):
    autenticar_como(superadmin.id)
    codigo = _codigo("pc")
    payload = {"codigo": codigo, "nombre": "A", "descuento_porcentaje": "10.00", "fecha_inicio": "2026-01-01"}
    assert client.post("/billing/planes-comerciales", json=payload).status_code == 201
    assert client.post("/billing/planes-comerciales", json=payload).status_code == 409


# ==========================================
# PLANES COMERCIALES: aplicabilidad (M2M módulos/planes)
# ==========================================
def test_plan_comercial_con_aplicabilidad_especifica_requiere_ids(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "X", "descuento_porcentaje": "10.00",
        "fecha_inicio": "2026-01-01", "aplica_a_todos_modulos": False,
    })
    assert r.status_code == 422


def test_plan_comercial_con_aplicabilidad_especifica_se_guarda_correctamente(client, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)

    r = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Sólo un módulo", "descuento_porcentaje": "10.00",
        "fecha_inicio": "2026-01-01", "aplica_a_todos_modulos": False, "modulos_ids": [modulo_id],
        "aplica_a_todos_planes": False, "planes_ids": [plan_id],
    })
    assert r.status_code == 201, r.text
    plan_comercial = r.json()
    assert plan_comercial["modulos_ids"] == [modulo_id]
    assert plan_comercial["planes_ids"] == [plan_id]


# ==========================================
# ASIGNACIÓN módulo+plan+descuento A UN TENANT
# ==========================================
def test_asigna_modulo_sin_descuento_precio_con_descuento_igual_a_base(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="500.00")
    metodo_pago_id = _crear_metodo_pago(client)

    r = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    assert r.status_code == 201, r.text
    asignacion = r.json()
    assert asignacion["precio_base"] == "500.00"
    assert asignacion["precio_con_descuento"] == "500.00"
    assert asignacion["estado"] == "activa"


def test_asigna_modulo_con_descuento_porcentaje_calcula_precio_correcto(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="1000.00")
    metodo_pago_id = _crear_metodo_pago(client)
    r_pc = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "20% off", "descuento_porcentaje": "20.00",
        "fecha_inicio": "2026-01-01",
    })
    plan_comercial_id = r_pc.json()["id"]

    r = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "plan_comercial_id": plan_comercial_id,
        "metodo_pago_id": metodo_pago_id, "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    assert r.status_code == 201, r.text
    assert r.json()["precio_base"] == "1000.00"
    assert r.json()["precio_con_descuento"] == "800.00"


def test_asigna_modulo_con_plan_comercial_bonificado_precio_con_descuento_cero(client, superadmin, tenant_a):
    """PRD §8: "100% bonificado ilimitado = deuda $0"."""
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="750.00")
    metodo_pago_id = _crear_metodo_pago(client)
    r_pc = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Cortesía", "es_bonificado": True,
        "fecha_inicio": "2026-01-01",
    })
    plan_comercial_id = r_pc.json()["id"]

    r = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "plan_comercial_id": plan_comercial_id,
        "metodo_pago_id": metodo_pago_id, "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    assert r.status_code == 201, r.text
    assert r.json()["precio_base"] == "750.00"
    assert r.json()["precio_con_descuento"] == "0.00"


def test_plan_comercial_no_aplicable_al_modulo_es_rechazado(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    otro_modulo_id, _otro_plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    r_pc = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Sólo otro módulo", "descuento_porcentaje": "10.00",
        "fecha_inicio": "2026-01-01", "aplica_a_todos_modulos": False, "modulos_ids": [otro_modulo_id],
    })
    plan_comercial_id = r_pc.json()["id"]

    r = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "plan_comercial_id": plan_comercial_id,
        "metodo_pago_id": metodo_pago_id, "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    assert r.status_code == 422


def test_no_se_puede_asignar_dos_veces_el_mismo_modulo_al_mismo_tenant(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    payload = {
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    }
    assert client.post(f"/billing/clientes/{tenant_a}/modulos", json=payload).status_code == 201
    assert client.post(f"/billing/clientes/{tenant_a}/modulos", json=payload).status_code == 409


def test_actualizar_plan_recalcula_precio_pero_no_cambiar_metodo_pago_no_lo_toca(client, superadmin, tenant_a):
    """El snapshot de precio sólo se recalcula cuando plan_id o
    plan_comercial_id cambian de verdad -- un cambio no relacionado
    (metodo_pago_id) no debe alterar precio_base/precio_con_descuento."""
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="200.00")
    metodo_pago_id = _crear_metodo_pago(client)
    otro_metodo_pago_id = _crear_metodo_pago(client)

    r = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    asignacion_id = r.json()["id"]

    # Sube el precio de lista DESPUÉS de asignar -- no debe afectar el snapshot ya tomado.
    client.put(f"/billing/planes/{plan_id}", json={"precio": "999.00"})

    r = client.put(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}", json={
        "metodo_pago_id": otro_metodo_pago_id,
    })
    assert r.status_code == 200, r.text
    assert r.json()["precio_base"] == "200.00"  # snapshot preservado
    assert r.json()["metodo_pago_id"] == otro_metodo_pago_id


def test_cambiar_plan_id_en_la_asignacion_recalcula_precio(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="100.00")
    r_plan2 = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "premium", "nombre": "Premium", "precio": "300.00",
    })
    plan2_id = r_plan2.json()["id"]
    metodo_pago_id = _crear_metodo_pago(client)

    r = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    asignacion_id = r.json()["id"]
    assert r.json()["precio_base"] == "100.00"

    r = client.put(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}", json={"plan_id": plan2_id})
    assert r.status_code == 200, r.text
    assert r.json()["precio_base"] == "300.00"
    assert r.json()["precio_con_descuento"] == "300.00"


def test_lista_y_elimina_asignacion_de_modulo(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    r = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    asignacion_id = r.json()["id"]

    r = client.get(f"/billing/clientes/{tenant_a}/modulos")
    assert r.status_code == 200
    assert any(a["id"] == asignacion_id for a in r.json())

    r = client.delete(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}")
    assert r.status_code == 204


def test_no_se_puede_eliminar_plan_comercial_con_clientes_asignados(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    r_pc = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Con cliente", "descuento_porcentaje": "10.00",
        "fecha_inicio": "2026-01-01",
    })
    plan_comercial_id = r_pc.json()["id"]
    client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "plan_comercial_id": plan_comercial_id,
        "metodo_pago_id": metodo_pago_id, "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })

    r = client.delete(f"/billing/planes-comerciales/{plan_comercial_id}")
    assert r.status_code == 409


def test_asignacion_a_tenant_inexistente_devuelve_404(client, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    r = client.post("/billing/clientes/tenant-que-no-existe/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    assert r.status_code == 404

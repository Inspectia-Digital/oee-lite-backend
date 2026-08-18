"""Fase ED (PRD "Billing MVP" v2.0): cálculo de monto a pagar. `Factura` es
un REGISTRO interno (nunca un documento real) generado por una ACCIÓN
explícita de Gerencia ("solicitar factura", mi-empresa) o SuperAdmin (panel
admin) -- nunca un cron (ver domain.py::Factura y billing.py::_generar_factura
para el porqué de esta resolución del PRD)."""
from datetime import date
import uuid

from tests.conftest import autenticar_como, crear_usuario
from app.models.domain import RolUsuario


def _codigo(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def _crear_modulo_y_plan(client, precio: str = "500.00"):
    r_modulo = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"})
    modulo_id = r_modulo.json()["id"]
    r_plan = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": precio,
    })
    return modulo_id, r_plan.json()["id"]


def _crear_metodo_pago(client) -> str:
    r = client.post("/billing/metodos-pago", json={
        "codigo": _codigo("mp"), "nombre": "Transferencia", "tipo": "transferencia",
    })
    return r.json()["id"]


def _asignar_modulo(client, tenant_id, modulo_id, plan_id, metodo_pago_id, plan_comercial_id=None):
    payload = {
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    }
    if plan_comercial_id:
        payload["plan_comercial_id"] = plan_comercial_id
    r = client.post(f"/billing/clientes/{tenant_id}/modulos", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ==========================================
# GENERACIÓN (admin-side y "cliente solicita")
# ==========================================
def test_no_superadmin_no_puede_generar_factura_desde_panel_admin(client, gerente_a, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)

    autenticar_como(gerente_a.id)
    r = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")
    # gerente_a no es superadmin -- el endpoint admin-side exige superadmin
    # (el de Gerencia es /mi-empresa/modulos/{id}/solicitar-factura).
    assert r.status_code == 403


def test_superadmin_genera_factura_para_cualquier_tenant(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="500.00")
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)

    r = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")
    assert r.status_code == 201, r.text
    factura = r.json()
    assert factura["tenant_id"] == tenant_a
    assert factura["asignacion_id"] == asignacion_id
    assert factura["monto"] == "500.00"
    assert factura["estado"] == "pendiente_envio"
    assert factura["numero"].startswith(f"FC-{date.today().year}-")
    assert factura["periodo"] == date.today().strftime("%Y-%m")


def test_gerencia_puede_solicitar_factura_de_su_propio_tenant(client, db, tenant_a):
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    # Necesita superadmin para armar el catálogo/asignación primero.
    superadmin_temp = crear_usuario(db, tenant_a, RolUsuario.SUPERADMIN)
    autenticar_como(superadmin_temp.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="250.00")
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)

    autenticar_como(gerente.id)
    r = client.post(f"/billing/mi-empresa/modulos/{asignacion_id}/solicitar-factura")
    assert r.status_code == 201, r.text
    assert r.json()["monto"] == "250.00"


def test_gerencia_no_puede_solicitar_factura_de_otro_tenant(client, db, tenant_a, tenant_b, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)

    gerente_b = crear_usuario(db, tenant_b, RolUsuario.GERENCIA)
    autenticar_como(gerente_b.id)
    r = client.post(f"/billing/mi-empresa/modulos/{asignacion_id}/solicitar-factura")
    assert r.status_code == 404


def test_no_se_puede_generar_dos_facturas_para_el_mismo_modulo_en_el_mismo_periodo(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)

    r1 = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")
    assert r1.status_code == 201
    r2 = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")
    assert r2.status_code == 409


def test_modulo_100_por_ciento_bonificado_genera_factura_pagada_monto_cero(client, superadmin, tenant_a):
    """PRD §8: "100% bonificado ilimitado = deuda $0, estado 'al día'
    siempre" -- acá se logra sin caso especial porque monto ya nace en 0."""
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="900.00")
    metodo_pago_id = _crear_metodo_pago(client)
    r_pc = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Cortesía", "es_bonificado": True,
        "fecha_inicio": "2026-01-01",
    })
    plan_comercial_id = r_pc.json()["id"]
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id, plan_comercial_id)

    r = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")
    assert r.status_code == 201, r.text
    assert r.json()["monto"] == "0.00"
    assert r.json()["estado"] == "pagada"

    r_cuenta = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r_cuenta.json()["estado_cuenta"] == "al_dia"
    assert r_cuenta.json()["deuda_total"] == "0.00"


def test_asignacion_suspendida_no_puede_generar_factura(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)
    client.put(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}", json={"estado": "suspendida"})

    r = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")
    assert r.status_code == 422


# ==========================================
# ESTADO DE CUENTA
# ==========================================
def test_estado_cuenta_con_deuda_tras_generar_factura_con_monto(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio="300.00")
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)
    client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")

    r = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r.status_code == 200
    assert r.json()["estado_cuenta"] == "con_deuda"
    assert r.json()["deuda_total"] == "300.00"


def test_tenant_sin_facturas_esta_al_dia(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    r = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r.status_code == 200
    assert r.json()["estado_cuenta"] == "al_dia"
    assert r.json()["deuda_total"] == "0.00"


def test_gerencia_ve_su_propio_estado_de_cuenta(client, db, tenant_a):
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    r = client.get("/billing/mi-empresa/estado-cuenta")
    assert r.status_code == 200
    assert r.json()["tenant_id"] == tenant_a


# ==========================================
# MARCAR ENVIADA
# ==========================================
def test_solo_superadmin_puede_marcar_factura_enviada(client, gerente_a, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)
    factura_id = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura").json()["id"]

    autenticar_como(gerente_a.id)
    r = client.post(f"/billing/facturas/{factura_id}/marcar-enviada")
    assert r.status_code == 403


def test_marcar_factura_enviada_actualiza_estado_y_metadata(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)
    factura_id = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura").json()["id"]

    r = client.post(f"/billing/facturas/{factura_id}/marcar-enviada")
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "enviada"
    assert r.json()["enviada_por_id"] == str(superadmin.id)
    assert r.json()["fecha_envio"] is not None


def test_no_se_puede_marcar_enviada_una_factura_ya_enviada(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)
    factura_id = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura").json()["id"]
    client.post(f"/billing/facturas/{factura_id}/marcar-enviada")

    r = client.post(f"/billing/facturas/{factura_id}/marcar-enviada")
    assert r.status_code == 422


# ==========================================
# LISTADOS + AISLAMIENTO DE TENANT
# ==========================================
def test_lista_facturas_de_tenant_filtra_por_estado(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)
    client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")

    r = client.get(f"/billing/clientes/{tenant_a}/facturas", params={"estado": "pendiente_envio"})
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.get(f"/billing/clientes/{tenant_a}/facturas", params={"estado": "pagada"})
    assert r.status_code == 200
    assert len(r.json()) == 0


def test_gerencia_de_un_tenant_no_ve_facturas_de_otro_via_mi_empresa(client, db, tenant_a, tenant_b, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_y_plan(client)
    metodo_pago_id = _crear_metodo_pago(client)
    asignacion_id = _asignar_modulo(client, tenant_a, modulo_id, plan_id, metodo_pago_id)
    client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")

    gerente_b = crear_usuario(db, tenant_b, RolUsuario.GERENCIA)
    autenticar_como(gerente_b.id)
    r = client.get("/billing/mi-empresa/facturas")
    assert r.status_code == 200
    assert r.json() == []

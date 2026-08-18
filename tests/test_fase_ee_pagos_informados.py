"""Fase EE (PRD "Billing MVP" v2.0): pagos informados -- el "autoinforme"
del cliente (informa que pagó una factura, con referencia/comprobante) +
aprobación/rechazo por SuperAdmin, con recálculo de deuda al aprobar."""
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


def _crear_factura(client, tenant_id, precio="500.00"):
    """Helper de punta a punta: módulo + plan + método de pago + asignación
    + factura generada, para tests que sólo necesitan una factura ya lista."""
    modulo_id, plan_id = _crear_modulo_y_plan(client, precio)
    metodo_pago_id = _crear_metodo_pago(client)
    r_asig = client.post(f"/billing/clientes/{tenant_id}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    asignacion_id = r_asig.json()["id"]
    r_fact = client.post(f"/billing/clientes/{tenant_id}/modulos/{asignacion_id}/generar-factura")
    return r_fact.json()["id"]


# ==========================================
# AUTOINFORME (mi-empresa)
# ==========================================
def test_gerencia_informa_pago_de_su_propia_factura(client, db, tenant_a):
    superadmin_temp = crear_usuario(db, tenant_a, RolUsuario.SUPERADMIN)
    autenticar_como(superadmin_temp.id)
    factura_id = _crear_factura(client, tenant_a, precio="500.00")

    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    r = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00", "referencia": "TRF-123",
    })
    assert r.status_code == 201, r.text
    pago = r.json()
    assert pago["estado"] == "pendiente_revision"
    assert pago["tenant_id"] == tenant_a
    assert pago["factura_id"] == factura_id


def test_no_se_puede_informar_pago_de_factura_de_otro_tenant(client, superadmin, tenant_a, tenant_b, db):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)

    gerente_b = crear_usuario(db, tenant_b, RolUsuario.GERENCIA)
    autenticar_como(gerente_b.id)
    r = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    })
    assert r.status_code == 404


def test_no_se_puede_informar_dos_pagos_pendientes_para_la_misma_factura(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)
    payload = {"fecha_pago": "2026-08-20", "monto": "500.00"}

    r1 = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json=payload)
    assert r1.status_code == 201
    r2 = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json=payload)
    assert r2.status_code == 409


def test_no_se_puede_informar_pago_de_factura_ya_pagada(client, superadmin, tenant_a):
    """El único caso donde generar_factura ya deja monto=0 -> pagada de
    entrada es 100% bonificado; más simple: usar marcar-enviada no basta,
    hay que llegar a 'pagada' -- se logra vía aprobar un pago primero."""
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)
    r_pago = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    })
    pago_id = r_pago.json()["id"]
    client.post(f"/billing/pagos-informados/{pago_id}/aprobar")

    r = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-21", "monto": "500.00",
    })
    assert r.status_code == 422


# ==========================================
# APROBACIÓN (recalcula deuda)
# ==========================================
def test_solo_superadmin_puede_aprobar_pago(client, gerente_a, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)
    pago_id = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    }).json()["id"]

    autenticar_como(gerente_a.id)
    r = client.post(f"/billing/pagos-informados/{pago_id}/aprobar")
    assert r.status_code == 403


def test_aprobar_pago_marca_factura_pagada_y_recalcula_deuda(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a, precio="500.00")

    r_cuenta = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r_cuenta.json()["deuda_total"] == "500.00"
    assert r_cuenta.json()["estado_cuenta"] == "con_deuda"

    pago_id = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00", "referencia": "TRF-999",
    }).json()["id"]

    r = client.post(f"/billing/pagos-informados/{pago_id}/aprobar")
    assert r.status_code == 200, r.text
    pago = r.json()
    assert pago["estado"] == "aprobado"
    assert pago["aprobado_por_id"] == str(superadmin.id)
    assert pago["fecha_aprobacion"] is not None

    r_factura = client.get(f"/billing/clientes/{tenant_a}/facturas")
    factura = next(f for f in r_factura.json() if f["id"] == factura_id)
    assert factura["estado"] == "pagada"

    r_cuenta = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r_cuenta.json()["deuda_total"] == "0.00"
    assert r_cuenta.json()["estado_cuenta"] == "al_dia"


def test_no_se_puede_aprobar_un_pago_ya_aprobado(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)
    pago_id = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    }).json()["id"]
    client.post(f"/billing/pagos-informados/{pago_id}/aprobar")

    r = client.post(f"/billing/pagos-informados/{pago_id}/aprobar")
    assert r.status_code == 422


# ==========================================
# RECHAZO
# ==========================================
def test_rechazar_pago_requiere_motivo(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)
    pago_id = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    }).json()["id"]

    r = client.post(f"/billing/pagos-informados/{pago_id}/rechazar", json={"motivo": ""})
    assert r.status_code == 422


def test_rechazar_pago_no_cambia_estado_de_factura_ni_deuda(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a, precio="500.00")
    pago_id = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    }).json()["id"]

    r = client.post(f"/billing/pagos-informados/{pago_id}/rechazar", json={"motivo": "Comprobante ilegible"})
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "rechazado"
    assert r.json()["observaciones"] == "Comprobante ilegible"

    r_factura = client.get(f"/billing/clientes/{tenant_a}/facturas")
    factura = next(f for f in r_factura.json() if f["id"] == factura_id)
    assert factura["estado"] == "pendiente_envio"  # sin cambios

    r_cuenta = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r_cuenta.json()["deuda_total"] == "500.00"  # sigue debiendo


def test_tras_rechazo_se_puede_informar_un_nuevo_pago(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)
    pago_id = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    }).json()["id"]
    client.post(f"/billing/pagos-informados/{pago_id}/rechazar", json={"motivo": "Monto incorrecto"})

    r = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-22", "monto": "500.00", "referencia": "TRF-corregido",
    })
    assert r.status_code == 201, r.text


# ==========================================
# LISTADOS + AISLAMIENTO
# ==========================================
def test_lista_pagos_informados_filtra_por_estado(client, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)
    client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    })

    r = client.get("/billing/pagos-informados", params={"estado": "pendiente_revision"})
    assert r.status_code == 200
    assert len(r.json()) >= 1
    assert all(p["estado"] == "pendiente_revision" for p in r.json())


def test_gerencia_de_un_tenant_no_ve_pagos_de_otro_via_mi_empresa(client, superadmin, tenant_a, tenant_b, db):
    autenticar_como(superadmin.id)
    factura_id = _crear_factura(client, tenant_a)
    client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-08-20", "monto": "500.00",
    })

    gerente_b = crear_usuario(db, tenant_b, RolUsuario.GERENCIA)
    autenticar_como(gerente_b.id)
    r = client.get("/billing/mi-empresa/pagos-informados")
    assert r.status_code == 200
    assert r.json() == []

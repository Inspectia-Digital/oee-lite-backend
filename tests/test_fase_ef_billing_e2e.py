"""Fase EF: Billing MVP completo -- auditoría de endpoints contra PRD §9 +
UN test de integración de punta a punta que encadena las 5 fases (EB-EE)
juntas, algo que ninguna de las suites individuales cubre (cada una testea
su propia fase de forma aislada). Corre contra Postgres real, como el
resto de la suite."""
import uuid

from tests.conftest import autenticar_como, crear_usuario
from app.models.domain import RolUsuario


def _codigo(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def test_ciclo_de_vida_completo_de_billing_con_descuento_y_pago(client, db, tenant_a, tenant_b):
    """Recorre el flujo real de punta a punta:
    catálogo (módulo+plan+método de pago) -> plan comercial con 30% off ->
    asignación a un tenant -> factura generada -> deuda con_deuda ->
    pago informado -> aprobado -> factura pagada -> deuda al_dia.
    Con un SEGUNDO tenant sin nada asignado, para confirmar aislamiento
    en cada paso (nunca ve ni afecta al primero)."""
    superadmin = crear_usuario(db, tenant_a, RolUsuario.SUPERADMIN)
    autenticar_como(superadmin.id)

    # 1. Catálogo global (Fase EB)
    r_modulo = client.post("/billing/modulos", json={
        "codigo": _codigo("mod"), "nombre": "TYMEO E2E", "orden": 1, "estado": "activo",
    })
    assert r_modulo.status_code == 201
    modulo_id = r_modulo.json()["id"]

    r_plan = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": "1000.00",
        "limite_usuarios": 15,
    })
    assert r_plan.status_code == 201
    plan_id = r_plan.json()["id"]

    r_metodo = client.post("/billing/metodos-pago", json={
        "codigo": _codigo("mp"), "nombre": "Transferencia bancaria", "tipo": "transferencia",
        "detalle": "Banco X, cuenta 123",
    })
    assert r_metodo.status_code == 201
    metodo_pago_id = r_metodo.json()["id"]

    # 2. Plan comercial: 30% de descuento (Fase EC)
    r_pc = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Descuento E2E 30%", "descuento_porcentaje": "30.00",
        "fecha_inicio": "2026-01-01",
    })
    assert r_pc.status_code == 201
    plan_comercial_id = r_pc.json()["id"]

    # 3. Asignación a tenant_a con el descuento (Fase EC)
    r_asig = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "plan_comercial_id": plan_comercial_id,
        "metodo_pago_id": metodo_pago_id, "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    assert r_asig.status_code == 201, r_asig.text
    asignacion_id = r_asig.json()["id"]
    assert r_asig.json()["precio_base"] == "1000.00"
    assert r_asig.json()["precio_con_descuento"] == "700.00"

    # tenant_b no tiene nada asignado todavía -- aislamiento desde el inicio.
    r_cuenta_b = client.get(f"/billing/clientes/{tenant_b}/estado-cuenta")
    assert r_cuenta_b.json()["deuda_total"] == "0.00"

    # 4. Generar factura (Fase ED) -- monto ya con el 30% aplicado.
    r_factura = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")
    assert r_factura.status_code == 201, r_factura.text
    factura = r_factura.json()
    assert factura["monto"] == "700.00"
    assert factura["estado"] == "pendiente_envio"
    factura_id = factura["id"]

    # La factura aparece en el listado GLOBAL de admin (nuevo en Fase EF).
    r_todas = client.get("/billing/facturas", params={"estado": "pendiente_envio"})
    assert r_todas.status_code == 200
    assert any(f["id"] == factura_id for f in r_todas.json())
    # tenant_b sigue sin facturas propias.
    r_facturas_b = client.get(f"/billing/clientes/{tenant_b}/facturas")
    assert r_facturas_b.json() == []

    # 5. Estado de cuenta refleja la deuda real (con descuento aplicado).
    r_cuenta = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r_cuenta.json()["deuda_total"] == "700.00"
    assert r_cuenta.json()["estado_cuenta"] == "con_deuda"

    # 6. Admin marca la factura como enviada.
    r_enviada = client.post(f"/billing/facturas/{factura_id}/marcar-enviada")
    assert r_enviada.status_code == 200
    assert r_enviada.json()["estado"] == "enviada"

    # 7. El cliente (Gerencia) informa el pago (Fase EE, "autoinforme").
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    r_pago = client.post(f"/billing/mi-empresa/facturas/{factura_id}/informar-pago", json={
        "fecha_pago": "2026-01-15", "monto": "700.00", "referencia": "TRF-E2E-001",
    })
    assert r_pago.status_code == 201, r_pago.text
    pago_id = r_pago.json()["id"]
    assert r_pago.json()["estado"] == "pendiente_revision"

    # El cliente ve su propio pago en mi-empresa; ve su propia factura como "enviada".
    r_mis_facturas = client.get("/billing/mi-empresa/facturas")
    assert any(f["id"] == factura_id and f["estado"] == "enviada" for f in r_mis_facturas.json())

    # 8. Admin aprueba el pago (Fase EE) -- recalcula deuda usando el
    # MISMO helper que generó la deuda inicial en el paso 5 (Fase ED).
    autenticar_como(superadmin.id)
    r_aprobar = client.post(f"/billing/pagos-informados/{pago_id}/aprobar")
    assert r_aprobar.status_code == 200, r_aprobar.text
    assert r_aprobar.json()["estado"] == "aprobado"

    # 9. Verificación final: factura pagada + estado de cuenta al día.
    r_facturas_finales = client.get(f"/billing/clientes/{tenant_a}/facturas")
    factura_final = next(f for f in r_facturas_finales.json() if f["id"] == factura_id)
    assert factura_final["estado"] == "pagada"

    r_cuenta_final = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r_cuenta_final.json()["deuda_total"] == "0.00"
    assert r_cuenta_final.json()["estado_cuenta"] == "al_dia"

    # tenant_b nunca se vio afectado por nada de lo anterior.
    r_cuenta_b_final = client.get(f"/billing/clientes/{tenant_b}/estado-cuenta")
    assert r_cuenta_b_final.json()["deuda_total"] == "0.00"
    assert client.get(f"/billing/clientes/{tenant_b}/modulos").json() == []


def test_ciclo_completo_100_por_ciento_bonificado_nunca_genera_deuda(client, db, tenant_a):
    """Segundo camino de punta a punta: plan comercial bonificado
    ilimitado -- la factura nace pagada, la deuda nunca sube de $0, y no
    hace falta ningún pago informado (PRD §8, "100% bonificado
    ilimitado = deuda $0, estado 'al día' siempre")."""
    superadmin = crear_usuario(db, tenant_a, RolUsuario.SUPERADMIN)
    autenticar_como(superadmin.id)

    modulo_id = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"}).json()["id"]
    plan_id = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": "1200.00",
    }).json()["id"]
    metodo_pago_id = client.post("/billing/metodos-pago", json={
        "codigo": _codigo("mp"), "nombre": "Transferencia", "tipo": "transferencia",
    }).json()["id"]
    plan_comercial_id = client.post("/billing/planes-comerciales", json={
        "codigo": _codigo("pc"), "nombre": "Cortesía ilimitada", "es_bonificado": True,
        "fecha_inicio": "2026-01-01",
    }).json()["id"]

    asignacion_id = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "plan_comercial_id": plan_comercial_id,
        "metodo_pago_id": metodo_pago_id, "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    }).json()["id"]

    r_factura = client.post(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}/generar-factura")
    assert r_factura.json()["monto"] == "0.00"
    assert r_factura.json()["estado"] == "pagada"

    r_cuenta = client.get(f"/billing/clientes/{tenant_a}/estado-cuenta")
    assert r_cuenta.json()["deuda_total"] == "0.00"
    assert r_cuenta.json()["estado_cuenta"] == "al_dia"
    assert r_cuenta.json()["facturas_vencidas"] == 0

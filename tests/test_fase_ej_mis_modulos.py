"""Fase EJ (frontend "Mi Empresa > Suscripción y Facturación"): endpoint
GET /billing/mi-empresa/modulos -- faltaba en el PRD §9 (que sólo lista el
admin-side) pero la maqueta cliente "MÓDULOS CONTRATADOS" (con precio/
descuento/renovación) lo necesita; mismo criterio de completitud que
`informar_pago`/`solicitar_factura` (Fases EE/ED)."""
import uuid

from tests.conftest import autenticar_como, crear_usuario
from app.models.domain import RolUsuario


def _codigo(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def test_gerencia_ve_sus_propios_modulos_asignados(client, db, tenant_a):
    superadmin = crear_usuario(db, tenant_a, RolUsuario.SUPERADMIN)
    autenticar_como(superadmin.id)

    r_modulo = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"})
    modulo_id = r_modulo.json()["id"]
    plan_id = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": "500.00",
    }).json()["id"]
    metodo_pago_id = client.post("/billing/metodos-pago", json={
        "codigo": _codigo("mp"), "nombre": "Transferencia", "tipo": "transferencia",
    }).json()["id"]
    client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })

    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    r = client.get("/billing/mi-empresa/modulos")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["modulo_id"] == modulo_id
    assert r.json()[0]["precio_con_descuento"] == "500.00"


def test_gerencia_de_un_tenant_no_ve_modulos_de_otro(client, db, tenant_a, tenant_b, superadmin):
    autenticar_como(superadmin.id)
    modulo_id = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "M"}).json()["id"]
    plan_id = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": "500.00",
    }).json()["id"]
    metodo_pago_id = client.post("/billing/metodos-pago", json={
        "codigo": _codigo("mp"), "nombre": "Transferencia", "tipo": "transferencia",
    }).json()["id"]
    client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })

    gerente_b = crear_usuario(db, tenant_b, RolUsuario.GERENCIA)
    autenticar_como(gerente_b.id)
    r = client.get("/billing/mi-empresa/modulos")
    assert r.status_code == 200
    assert r.json() == []


def test_operario_no_puede_ver_mis_modulos(client, db, tenant_a):
    operario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    autenticar_como(operario.id)
    r = client.get("/billing/mi-empresa/modulos")
    assert r.status_code == 403

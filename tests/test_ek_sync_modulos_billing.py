"""Fase EK.3 (plan "Unificación mínima de módulos", ago 2026): tests de
`_sincronizar_modulos_contratados` (billing.py) -- la conexión nueva entre
Sistema B (Billing, `AsignacionModuloTenant`) y Sistema A (CSV legado,
`Tenant.modulos_contratados`, lo que gobierna la navegación real).

Incluye la reproducción exacta del bug original reportado por el usuario:
con la DB recién migrada, sin correr `seed.py` a mano, el switch de
asignar módulos devolvía 422 "Módulos inválidos: [...]. Válidos: []"
porque `modulos_disponibles` quedaba vacía (el único seed real era un
script opcional de desarrollo). La migración `a1c3e8f56b02` (Fase EK.1)
lo arregla corriendo siempre vía `alembic upgrade head` -- este test NO
llama a ningún seed manual, a propósito, para probar justamente eso."""
import uuid
from datetime import date

from tests.conftest import autenticar_como
from app.models.domain import Tenant


def _codigo(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def _crear_modulo_plan_metodo(client) -> tuple[str, str, str]:
    """Catálogo mínimo para poder asignar un módulo de billing -- usa los
    endpoints reales (Fase EB/EC), no inserts directos, para probar el
    flujo tal cual lo usaría el Panel SaaS."""
    r_modulo = client.post("/billing/modulos", json={
        "codigo": _codigo("mod"), "nombre": "Módulo EK", "orden": 9, "estado": "activo",
    })
    assert r_modulo.status_code == 201, r_modulo.text
    modulo_id = r_modulo.json()["id"]

    r_plan = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": "500.00",
    })
    assert r_plan.status_code == 201, r_plan.text
    plan_id = r_plan.json()["id"]

    r_metodo = client.post("/billing/metodos-pago", json={
        "codigo": _codigo("mp"), "nombre": "Transferencia", "tipo": "transferencia",
    })
    assert r_metodo.status_code == 201, r_metodo.text
    metodo_pago_id = r_metodo.json()["id"]

    return modulo_id, plan_id, metodo_pago_id


def _codigo_del_modulo(db, modulo_id: str) -> str:
    from app.models.domain import ModuloDisponible
    return db.get(ModuloDisponible, uuid.UUID(modulo_id)).codigo


def test_catalogo_recien_migrado_sin_seed_manual_funciona(client, db, tenant_a, superadmin):
    """Reproducción exacta del bug original (ver docstring del módulo).
    Este test NO siembra nada a mano -- si la migración a1c3e8f56b02 no
    corrió (o el bug reapareciera), `GET /billing/modulos` no traería
    'tymeo' y el PATCH de abajo devolvería 422 en vez de 200."""
    autenticar_como(superadmin.id)

    r_catalogo = client.get("/billing/modulos")
    assert r_catalogo.status_code == 200
    codigos = {m["codigo"] for m in r_catalogo.json()}
    assert "tymeo" in codigos, "el seed real (migración EK.1) no corrió -- catálogo vacío"

    r_patch = client.patch(
        f"/accesos/superadmin/tenants/{tenant_a}/modulos",
        json={"modulos_contratados": ["tymeo"]},
    )
    assert r_patch.status_code == 200, r_patch.text


def test_asignar_modulo_billing_otorga_acceso_en_tenant(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id, metodo_pago_id = _crear_modulo_plan_metodo(client)
    codigo = _codigo_del_modulo(db, modulo_id)

    antes = db.get(Tenant, tenant_a).modulos_contratados
    assert codigo not in (antes or "").split(",")

    r_asig = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    assert r_asig.status_code == 201, r_asig.text

    db.expire_all()
    despues = db.get(Tenant, tenant_a).modulos_contratados
    assert codigo in despues.split(",")


def test_suspender_asignacion_quita_acceso(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id, metodo_pago_id = _crear_modulo_plan_metodo(client)
    codigo = _codigo_del_modulo(db, modulo_id)

    r_asig = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    asignacion_id = r_asig.json()["id"]
    db.expire_all()
    assert codigo in db.get(Tenant, tenant_a).modulos_contratados.split(",")

    r_put = client.put(
        f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}",
        json={"estado": "suspendida"},
    )
    assert r_put.status_code == 200, r_put.text

    db.expire_all()
    despues = db.get(Tenant, tenant_a).modulos_contratados
    assert codigo not in (despues or "").split(",")


def test_eliminar_asignacion_quita_acceso(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id, metodo_pago_id = _crear_modulo_plan_metodo(client)
    codigo = _codigo_del_modulo(db, modulo_id)

    r_asig = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    asignacion_id = r_asig.json()["id"]
    db.expire_all()
    assert codigo in db.get(Tenant, tenant_a).modulos_contratados.split(",")

    r_del = client.delete(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}")
    assert r_del.status_code == 204

    db.expire_all()
    despues = db.get(Tenant, tenant_a).modulos_contratados
    assert codigo not in (despues or "").split(",")


def test_sync_no_pisa_tymeo(client, db, tenant_a, superadmin):
    """El caso que protege a Green Mills en producción: 'tymeo' es el
    default gratuito de todo tenant nuevo (Field(default="tymeo") en
    Tenant) y NUNCA pasa por una AsignacionModuloTenant -- el helper de
    sincronización no debe tocarlo jamás, ni al asignar ni al eliminar
    un módulo de billing distinto."""
    autenticar_como(superadmin.id)
    t = db.get(Tenant, tenant_a)
    assert "tymeo" in t.modulos_contratados.split(",")

    modulo_id, plan_id, metodo_pago_id = _crear_modulo_plan_metodo(client)

    r_asig = client.post(f"/billing/clientes/{tenant_a}/modulos", json={
        "modulo_id": modulo_id, "plan_id": plan_id, "metodo_pago_id": metodo_pago_id,
        "fecha_inicio": "2026-01-01", "fecha_renovacion": "2026-02-01",
    })
    asignacion_id = r_asig.json()["id"]
    db.expire_all()
    assert "tymeo" in db.get(Tenant, tenant_a).modulos_contratados.split(",")

    r_del = client.delete(f"/billing/clientes/{tenant_a}/modulos/{asignacion_id}")
    assert r_del.status_code == 204

    db.expire_all()
    assert "tymeo" in db.get(Tenant, tenant_a).modulos_contratados.split(",")

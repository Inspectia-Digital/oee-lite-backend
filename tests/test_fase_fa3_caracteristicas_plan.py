"""Fase FA.3 (PRD Demo/Partners/Marketplace/Soporte/Planes): submódulos/
features por plan. Cubre CaracteristicaModulo (CRUD SuperAdmin),
asignación a un PlanPrecio (PUT reemplaza el set completo, valida que
la característica sea del MISMO módulo del plan) y el listado plano
GET /billing/planes (gap real que bloqueaba el selector de planes
específicos de PlanComercial -- ver comentario en BillingConfigTab.tsx)."""
import uuid

from tests.conftest import autenticar_como


def _codigo(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def _crear_modulo_con_plan(client) -> tuple[str, str]:
    r = client.post("/billing/modulos", json={"codigo": _codigo("mod"), "nombre": "Módulo Test"})
    modulo_id = r.json()["id"]
    r = client.post(f"/billing/modulos/{modulo_id}/planes", json={
        "modulo_id": modulo_id, "codigo": "pro", "nombre": "Pro", "precio": "100.00",
    })
    return modulo_id, r.json()["id"]


def test_no_superadmin_no_puede_crear_caracteristica(client, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/billing/modulos/00000000-0000-0000-0000-000000000000/caracteristicas", json={
        "codigo": "x", "nombre": "X",
    })
    assert r.status_code == 403


def test_crear_listar_y_eliminar_caracteristica(client, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, _ = _crear_modulo_con_plan(client)

    r = client.post(f"/billing/modulos/{modulo_id}/caracteristicas", json={
        "codigo": "exportacion_excel", "nombre": "Exportación a Excel", "descripcion": "Descarga reportes en xlsx",
    })
    assert r.status_code == 201, r.text
    caracteristica = r.json()
    assert caracteristica["codigo"] == "exportacion_excel"

    r = client.get(f"/billing/modulos/{modulo_id}/caracteristicas")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.delete(f"/billing/caracteristicas/{caracteristica['id']}")
    assert r.status_code == 204

    r = client.get(f"/billing/modulos/{modulo_id}/caracteristicas")
    assert r.json() == []


def test_codigo_duplicado_dentro_del_mismo_modulo_devuelve_409(client, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, _ = _crear_modulo_con_plan(client)
    client.post(f"/billing/modulos/{modulo_id}/caracteristicas", json={"codigo": "dup", "nombre": "Dup"})
    r = client.post(f"/billing/modulos/{modulo_id}/caracteristicas", json={"codigo": "dup", "nombre": "Otra"})
    assert r.status_code == 409


def test_asignar_caracteristicas_a_un_plan(client, superadmin):
    autenticar_como(superadmin.id)
    modulo_id, plan_id = _crear_modulo_con_plan(client)
    c1 = client.post(f"/billing/modulos/{modulo_id}/caracteristicas", json={"codigo": "c1", "nombre": "C1"}).json()
    c2 = client.post(f"/billing/modulos/{modulo_id}/caracteristicas", json={"codigo": "c2", "nombre": "C2"}).json()

    r = client.put(f"/billing/planes/{plan_id}/caracteristicas", json={"caracteristicas_ids": [c1["id"], c2["id"]]})
    assert r.status_code == 200, r.text
    assert {c["id"] for c in r.json()} == {c1["id"], c2["id"]}

    r = client.get(f"/billing/planes/{plan_id}/caracteristicas")
    assert r.status_code == 200
    assert len(r.json()) == 2

    # Reemplaza el set completo -- sólo c1 queda.
    r = client.put(f"/billing/planes/{plan_id}/caracteristicas", json={"caracteristicas_ids": [c1["id"]]})
    assert r.status_code == 200
    assert [c["id"] for c in r.json()] == [c1["id"]]


def test_no_se_puede_asignar_caracteristica_de_otro_modulo(client, superadmin):
    autenticar_como(superadmin.id)
    _modulo_a, plan_a = _crear_modulo_con_plan(client)
    modulo_b, _plan_b = _crear_modulo_con_plan(client)
    c_de_b = client.post(f"/billing/modulos/{modulo_b}/caracteristicas", json={"codigo": "cb", "nombre": "CB"}).json()

    r = client.put(f"/billing/planes/{plan_a}/caracteristicas", json={"caracteristicas_ids": [c_de_b["id"]]})
    assert r.status_code == 422


def test_listar_todos_los_planes_devuelve_modulo_nombre(client, superadmin):
    """Gap real que bloqueaba el selector de planes de PlanComercial --
    antes sólo existía GET /billing/modulos/{id}/planes (anidado)."""
    autenticar_como(superadmin.id)
    _modulo_id, plan_id = _crear_modulo_con_plan(client)

    r = client.get("/billing/planes")
    assert r.status_code == 200
    fila = next((p for p in r.json() if p["id"] == plan_id), None)
    assert fila is not None
    assert fila["modulo_nombre"] == "Módulo Test"
    assert fila["codigo"] == "pro"

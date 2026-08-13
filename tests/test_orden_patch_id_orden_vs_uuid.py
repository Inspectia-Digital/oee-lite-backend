"""Regresión: PlanesPanel.tsx (frontend) ganó edición de Orden dentro de
un Plan. El viejo OrdenesPanel.tsx (retirado en "feat(unificacion-planes)")
armaba `editId` con el UUID interno de la fila -- pero PATCH/GET/DELETE
/config/ordenes/{id_orden} resuelven la orden por `id_orden` (clave de
negocio, PK legacy durante la fase expand, ver nota en OrdenProduccion),
no por `OrdenProduccion.id` (UUID interno, expuesto aparte en
OrdenEnPlan.id). Mandar el UUID donde se espera id_orden nunca hace
match -- 404 silencioso desde el punto de vista de la UI.

Este archivo fija el contrato para que PlanesPanel.tsx (que sí tiene
ambos campos disponibles por fila via OrdenEnPlan) no repita el error.
"""
import uuid
from datetime import date

from app.models.domain import Linea, Planta
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta orden-patch")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea orden-patch")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    return planta, linea


def _crear_plan(client, headers, linea_id, nombre):
    r = client.post(
        "/config/planes/",
        json={"linea_id": str(linea_id), "fecha_inicio": date.today().isoformat(), "nombre": nombre},
        headers=headers,
    )
    assert r.status_code == 201
    return r.json()


def _crear_orden_en_plan(client, headers, linea_id, plan_id):
    id_orden = f"OP-PATCH-{uuid.uuid4().hex[:6]}"
    r = client.post(
        "/config/ordenes/",
        json={
            "id_orden": id_orden,
            "linea_id": str(linea_id),
            "cantidad_esperada": 100,
            "plan_id": str(plan_id),
        },
        headers=headers,
    )
    assert r.status_code == 201
    return r.json()


def test_orden_en_plan_expone_id_orden_e_id_uuid_como_campos_distintos(client, db, tenant_a, gerente_a):
    """Precondición del bug: el detalle del plan (lo que renderiza la
    tabla de PlanesPanel.tsx) trae AMBOS campos por fila, y no son
    intercambiables."""
    planta, linea = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    plan = _crear_plan(client, headers, linea.id, "Plan campos")
    orden = _crear_orden_en_plan(client, headers, linea.id, plan["id"])

    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    (fila,) = detalle["ordenes"]
    assert fila["id_orden"] == orden["id_orden"]
    uuid.UUID(fila["id"])  # es un UUID de verdad, no otro alias de id_orden
    assert fila["id"] != fila["id_orden"]


def test_patch_orden_con_uuid_interno_devuelve_404(client, db, tenant_a, gerente_a):
    """Reproduce el bug histórico: si `editId` se arma con el UUID interno
    (como hacía el viejo OrdenesPanel.tsx via editingItem.id) el backend
    nunca lo matchea contra id_orden -- 404."""
    planta, linea = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    plan = _crear_plan(client, headers, linea.id, "Plan bug uuid")
    orden = _crear_orden_en_plan(client, headers, linea.id, plan["id"])

    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    (fila,) = detalle["ordenes"]

    r = client.patch(
        f"/config/ordenes/{fila['id']}",  # BUG: UUID en vez de id_orden
        json={"cantidad_esperada": 999},
        headers=headers,
    )
    assert r.status_code == 404

    # Confirma que ni siquiera tocó la orden real.
    intacta = client.get(f"/config/ordenes/{orden['id_orden']}", headers=headers).json()
    assert intacta["cantidad_esperada"] == 100


def test_patch_orden_con_id_orden_persiste_cantidad_y_estado_end_to_end(client, db, tenant_a, gerente_a):
    """El fix: PATCH con id_orden (lo que ahora manda PlanesPanel.tsx como
    editId) sí encuentra la orden y el cambio persiste -- confirmado con
    un GET posterior a la orden y al detalle del plan."""
    planta, linea = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    plan = _crear_plan(client, headers, linea.id, "Plan fix id_orden")
    orden = _crear_orden_en_plan(client, headers, linea.id, plan["id"])

    r = client.patch(
        f"/config/ordenes/{orden['id_orden']}",
        json={"cantidad_esperada": 250, "estado": "en_progreso"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cantidad_esperada"] == 250
    assert body["estado"] == "en_progreso"

    releida = client.get(f"/config/ordenes/{orden['id_orden']}", headers=headers).json()
    assert releida["cantidad_esperada"] == 250
    assert releida["estado"] == "en_progreso"

    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    (fila,) = [o for o in detalle["ordenes"] if o["id_orden"] == orden["id_orden"]]
    assert fila["cantidad_esperada"] == 250
    assert fila["estado"] == "en_progreso"

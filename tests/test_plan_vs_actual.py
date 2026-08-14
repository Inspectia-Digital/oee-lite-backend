"""Fase N (auditoría de producción del front, ítem #7): Plan vs Actual.
Antes esta vista era mock siempre en el front -- no existía ningún
endpoint que cruzara OrdenProduccion.cantidad_esperada (plan) contra la
producción real acumulada por orden."""
import uuid
from datetime import date, datetime, timedelta

from app.models.domain import LiteEventoProduccion, Linea, MaestroSKU, OrdenProduccion, Planta
from tests.conftest import autenticar_como


def _preparar_linea(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Plan")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Plan")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    return planta, linea


def _crear_orden(db, tenant_id, linea_id, cantidad_esperada, plan_fecha, id_orden=None):
    orden = OrdenProduccion(
        tenant_id=tenant_id, id_orden=id_orden or f"OP-{uuid.uuid4().hex[:8]}",
        linea_id=linea_id, cantidad_esperada=cantidad_esperada, plan_fecha=plan_fecha,
    )
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden


def _emitir_evento(db, tenant_id, orden_fk, unidades):
    evento = LiteEventoProduccion(
        tenant_id=tenant_id, id_estacion="EST-PLAN", orden_fk=orden_fk,
        unidades_procesadas=unidades,
    )
    db.add(evento)
    db.commit()


def test_plan_vs_actual_cruza_esperado_y_producido(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    hoy = date.today().isoformat()
    orden = _crear_orden(db, tenant_a, linea.id, cantidad_esperada=100, plan_fecha=hoy)
    _emitir_evento(db, tenant_a, orden.id_orden, 30)
    _emitir_evento(db, tenant_a, orden.id_orden, 25)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/plan-vs-actual/",
        params={"fecha_desde": hoy, "fecha_hasta": hoy},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["id_orden"] == orden.id_orden
    assert filas[0]["cantidad_esperada"] == 100
    assert filas[0]["cantidad_producida"] == 55
    assert filas[0]["cumplimiento_pct"] == 55.0


def test_plan_vs_actual_orden_sin_eventos_da_cero_producido(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    hoy = date.today().isoformat()
    _crear_orden(db, tenant_a, linea.id, cantidad_esperada=50, plan_fecha=hoy)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/plan-vs-actual/",
        params={"fecha_desde": hoy, "fecha_hasta": hoy},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()[0]["cantidad_producida"] == 0


def test_plan_vs_actual_filtra_por_rango_de_fecha_del_plan(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    hoy = date.today()
    _crear_orden(db, tenant_a, linea.id, cantidad_esperada=10, plan_fecha=hoy.isoformat())
    _crear_orden(db, tenant_a, linea.id, cantidad_esperada=20, plan_fecha=(hoy - timedelta(days=30)).isoformat())

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/plan-vs-actual/",
        params={"fecha_desde": hoy.isoformat(), "fecha_hasta": hoy.isoformat()},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_plan_vs_actual_no_mezcla_eventos_de_otro_tenant(client, db, tenant_a, tenant_b, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    planta_b, linea_b = _preparar_linea(db, tenant_b)
    hoy = date.today().isoformat()

    id_orden_compartido = f"OP-COMPARTIDA-{uuid.uuid4().hex[:8]}"
    orden = _crear_orden(db, tenant_a, linea.id, cantidad_esperada=10, plan_fecha=hoy, id_orden=id_orden_compartido)
    # Un evento de otro tenant con el mismo id_orden (colisión posible ya
    # que id_orden sigue siendo PK legacy global) no debe sumar acá.
    _emitir_evento(db, tenant_b, id_orden_compartido, 999)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/plan-vs-actual/",
        params={"fecha_desde": hoy, "fecha_hasta": hoy},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()[0]["cantidad_producida"] == 0


def test_plan_vs_actual_fecha_hasta_menor_a_desde_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/plan-vs-actual/",
        params={"fecha_desde": "2026-01-10", "fecha_hasta": "2026-01-01"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_plan_vs_actual_rechaza_rango_mayor_a_90_dias(client, db, tenant_a, gerente_a):
    """Fase BU: mismo tope de robustez que reporte-produccion/oee-tendencia."""
    planta, _ = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/plan-vs-actual/",
        params={"fecha_desde": "2026-01-01", "fecha_hasta": "2026-06-01"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400
    assert "90" in r.json()["detail"]


def test_plan_vs_actual_sin_planta_devuelve_vacio(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/plan-vs-actual/",
        params={"fecha_desde": "2026-01-01", "fecha_hasta": "2026-01-31"},
    )
    assert r.status_code == 200
    assert r.json() == []

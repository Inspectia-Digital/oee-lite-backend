"""Fase AE (pedido de Green Mills, "pestaña Análisis por SKU"):
/analytics/rendimiento-sku/ -- reusa _calcular_metricas_oee (mismas 4
métricas de siempre: Disponibilidad/Rendimiento/Calidad/OEE), agrupado
por SKU en vez de agregado. La fórmula en sí ya está probada
exhaustivamente donde vive (Fase E2/Q); acá se prueba el mecanismo
nuevo -- que el filtro por sku_fk aísla correctamente los eventos de
cada SKU, sin mezclarlos.
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import Estacion, EstadoOrden, Linea, MaestroSKU, OrdenProduccion, Planta
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AE")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea AE", tiempo_ideal_seg=5, tiempo_lento_seg=500, tiempo_alerta_seg=1000)
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación AE", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def _crear_orden_en_progreso(db, tenant_id, linea_id, codigo_sku, descripcion=None):
    sku = MaestroSKU(
        tenant_id=tenant_id, codigo_sku=codigo_sku, descripcion=descripcion or f"SKU {codigo_sku}",
        tiempo_ideal_seg=5, tiempo_lento_seg=500, tiempo_alerta_seg=1000,
    )
    db.add(sku)
    db.commit()
    orden = OrdenProduccion(
        tenant_id=tenant_id, id_orden=f"OP-{uuid.uuid4().hex[:8]}", linea_id=linea_id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=codigo_sku,
    )
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden


def _cerrar(db, orden):
    # id_orden sigue siendo la PK real (C1/C2) -- db.get(Model, orden.id)
    # rompería con "character varying = uuid" (id_orden es varchar, id es
    # sólo unique, no PK).
    orden_db = db.get(OrdenProduccion, orden.id_orden)
    orden_db.estado = EstadoOrden.CERRADA
    db.add(orden_db)
    db.commit()


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _postear_evento(client, credencial, estacion_id, ts):
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id), "timestamp": ts.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    return r


def test_rendimiento_sku_distingue_dos_skus(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    orden_a = _crear_orden_en_progreso(db, tenant_a, linea.id, f"SKU-AE-A-{uuid.uuid4().hex[:6]}")
    ahora = datetime.now(timezone.utc) - timedelta(hours=2)
    _postear_evento(client, credencial, estacion.id, ahora)
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=5))

    # Cierra A, activa B -- scans.py resuelve por "última orden EN_PROGRESO
    # de la línea"; sin esto, con dos EN_PROGRESO a la vez el resultado
    # sería ambiguo.
    _cerrar(db, orden_a)
    codigo_b = f"SKU-AE-B-{uuid.uuid4().hex[:6]}"
    _crear_orden_en_progreso(db, tenant_a, linea.id, codigo_b, descripcion="Producto B")
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(minutes=10))
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(minutes=10, seconds=5))

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-sku/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 2
    por_sku = {f["sku_fk"]: f for f in filas}
    assert por_sku[orden_a.sku_fk]["unidades_producidas"] == 2
    assert por_sku[codigo_b]["unidades_producidas"] == 2
    assert por_sku[codigo_b]["sku_descripcion"] == "Producto B"
    # Sin paradas ni lentitud -- las 4 métricas dan 100% para los dos.
    assert por_sku[codigo_b]["rendimiento_pct"] == 100.0
    assert por_sku[codigo_b]["disponibilidad_pct"] == 100.0


def test_rendimiento_sku_ordena_peor_primero(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    orden_bueno = _crear_orden_en_progreso(db, tenant_a, linea.id, f"SKU-AE-BUENO-{uuid.uuid4().hex[:6]}")
    ahora = datetime.now(timezone.utc) - timedelta(hours=2)
    _postear_evento(client, credencial, estacion.id, ahora)
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=5))  # OPTIMO
    _cerrar(db, orden_bueno)

    # SKU malo: segundo evento LENTO (delta=520s > tiempo_lento_seg=500,
    # < tiempo_alerta_seg=1000 -- LENTO, no ALERTA/parada).
    codigo_malo = f"SKU-AE-MALO-{uuid.uuid4().hex[:6]}"
    _crear_orden_en_progreso(db, tenant_a, linea.id, codigo_malo)
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(minutes=10))
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(minutes=10, seconds=520))

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-sku/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 2
    # Peor OEE primero.
    assert filas[0]["sku_fk"] == codigo_malo
    assert filas[0]["rendimiento_pct"] < 100.0
    assert filas[1]["rendimiento_pct"] == 100.0


def test_rendimiento_sku_sin_orden_activa_no_aparece(client, db, tenant_a, gerente_a):
    """Un evento sin ninguna orden EN_PROGRESO resuelta no puede
    atribuirse a ningún SKU -- no aparece (no se inventa una fila)."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    _postear_evento(client, credencial, estacion.id, datetime.now(timezone.utc))

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/rendimiento-sku/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_rendimiento_sku_no_contamina_disponibilidad_con_paradas_de_otro_sku(client, db, tenant_a, gerente_a):
    """QA-03 (auditoría QA): antes _calcular_metricas_oee filtraba
    `eventos` por sku_fk pero nunca `paradas` -- si SKU-A generaba una
    parada real (ALERTA), esa parada contaminaba también la
    disponibilidad de SKU-B, que no tuvo ninguna. Ahora cada SKU sólo ve
    las paradas de SUS PROPIAS órdenes."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc) - timedelta(hours=3)

    # SKU-A: gap de 1200s entre eventos (> tiempo_alerta_seg=1000) --
    # genera una ParadaDetectada real, atada a la orden de A.
    orden_a = _crear_orden_en_progreso(db, tenant_a, linea.id, f"SKU-AJ-A-{uuid.uuid4().hex[:6]}")
    _postear_evento(client, credencial, estacion.id, ahora)
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=1200))
    _cerrar(db, orden_a)

    # SKU-B: eventos seguidos, sin ninguna parada.
    codigo_b = f"SKU-AJ-B-{uuid.uuid4().hex[:6]}"
    _crear_orden_en_progreso(db, tenant_a, linea.id, codigo_b)
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(minutes=30))
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(minutes=30, seconds=5))

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-sku/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    por_sku = {f["sku_fk"]: f for f in r.json()}
    assert por_sku[orden_a.sku_fk]["disponibilidad_pct"] < 100.0  # la parada es suya
    assert por_sku[orden_a.sku_fk]["minutos_perdidos"] > 0
    # El bug real: sin el fix, esto también daba < 100 -- SKU-B heredaba
    # la parada de SKU-A porque `q_paradas` no filtraba por orden/SKU.
    assert por_sku[codigo_b]["disponibilidad_pct"] == 100.0
    assert por_sku[codigo_b]["minutos_perdidos"] == 0

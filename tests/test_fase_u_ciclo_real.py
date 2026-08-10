"""Fase U: /analytics/cuellos-botella/ y /analytics/rendimiento-secuencial/
excluyen del promedio los eventos que fueron el primer ping de una nueva
sesión de orden (mismo_contexto_de_orden=False en scans.py) -- ese
delta_t_segundos es el hueco real SIN clasificar ni capear (puede ser de
horas: cambio de turno, corte de sesión de prueba), y promediarlo con el
resto arruina "tiempo de ciclo promedio" aunque la tolerancia esté bien
configurada. Bug real encontrado auditando el dashboard de Green Mills:
Cuellos de Botella seguía mostrando un desvío enorme después de corregir
la tolerancia de la estación -- la causa no tenía nada que ver con la
tolerancia.
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import (
    Estacion, EstadoOrden, Linea, MaestroSKU, OrdenProduccion, Planta,
    TipoProduccion,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta U")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea U", tipo_produccion=TipoProduccion.DISCRETA)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Armadora", tipo="sensor", linea_id=linea.id, activa=True, posicion_linea=1)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    return planta, linea, estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _crear_orden_en_progreso(db, tenant_id, linea_id, tiempo_ciclo_teorico=10.0):
    sku = MaestroSKU(
        tenant_id=tenant_id, codigo_sku=f"SKU-{uuid.uuid4().hex[:8]}",
        descripcion="SKU de prueba", tiempo_ciclo_teorico=tiempo_ciclo_teorico, unidades_por_ciclo=1,
    )
    db.add(sku)
    db.commit()
    orden = OrdenProduccion(
        tenant_id=tenant_id, id_orden=f"OP-{uuid.uuid4().hex[:8]}", linea_id=linea_id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=sku.codigo_sku,
    )
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden, sku


def _postear_evento(client, credencial, estacion_id, ts):
    return client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id), "timestamp": ts.isoformat()},
        headers={"X-Device-Key": credencial},
    )


def _construir_escenario_con_transicion(client, db, tenant_a, gerente_a):
    """3 ciclos reales de 10s (dos dentro de orden1, uno recién abierta
    orden2) + 1 transición de 4hs entre orden1 y orden2. Promedio real
    correcto = 10s; promedio roto (sin excluir la transición) = 3607.5s."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden1, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ciclo_teorico=10.0)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=6)  # margen para que +4h no caiga en el futuro
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201  # 1er evento (delta=0)
    ts2 = ahora + timedelta(seconds=10)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201  # ciclo real #1 (delta=10)
    ts3 = ts2 + timedelta(seconds=10)
    assert _postear_evento(client, credencial, estacion.id, ts3).status_code == 201  # ciclo real #2 (delta=10)

    orden1.estado = EstadoOrden.CERRADA
    db.add(orden1)
    db.commit()
    orden2, _ = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ciclo_teorico=10.0)

    ts4 = ts3 + timedelta(hours=4)  # transición de sesión: hueco real de 4hs
    assert _postear_evento(client, credencial, estacion.id, ts4).status_code == 201
    ts5 = ts4 + timedelta(seconds=10)
    assert _postear_evento(client, credencial, estacion.id, ts5).status_code == 201  # ciclo real #3 (delta=10)

    return planta, linea, estacion


def test_cuellos_botella_excluye_transicion_de_orden_del_promedio(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _construir_escenario_con_transicion(client, db, tenant_a, gerente_a)

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/cuellos-botella/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    # Sin excluir la transición el promedio sería (10+10+14400+10)/4=3607.5s.
    assert filas[0]["tiempo_promedio_real_seg"] == 10.0
    assert filas[0]["desvio_pct"] == 0.0  # esperado == real, ambos SKU tiempo_ciclo_teorico=10


def test_rendimiento_secuencial_excluye_transicion_de_orden_del_promedio(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _construir_escenario_con_transicion(client, db, tenant_a, gerente_a)

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-secuencial/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["tiempo_ciclo_prom"] == 10.0
    assert filas[0]["objetivo"] == 10.0


def test_cuellos_botella_transicion_de_orden_entre_estaciones_distintas_no_se_confunde(client, db, tenant_a, gerente_a):
    """El seguimiento de "orden previo" es POR ESTACIÓN -- una transición
    real en la Estación A no debe hacer que un evento normal de la
    Estación B (que nunca cambió de orden) se excluya por error."""
    planta = Planta(tenant_id=tenant_a, nombre="Planta U2")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea U2", tipo_produccion=TipoProduccion.DISCRETA)
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion_b = Estacion(tenant_id=tenant_a, nombre="Estación B", tipo="sensor", linea_id=linea.id, activa=True, posicion_linea=1)
    db.add(estacion_b)
    db.commit()
    db.refresh(estacion_b)

    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ciclo_teorico=10.0)
    credencial_b = _emitir_credencial(client, gerente_a, estacion_b.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    assert _postear_evento(client, credencial_b, estacion_b.id, ahora).status_code == 201
    ts2 = ahora + timedelta(seconds=10)
    assert _postear_evento(client, credencial_b, estacion_b.id, ts2).status_code == 201

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-secuencial/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["tiempo_ciclo_prom"] == 10.0

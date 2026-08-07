"""Fase P: ingesta PLC ciego (Green Mills).

Estacion.orden_activa_fk / sku_activo_fk nunca los escribe nadie (siguen
NULL siempre) -- para un PLC sin codigo_pieza, la ÚNICA fuente de orden/SKU
activo es la OrdenProduccion EN_PROGRESO de la línea (misma resolución que
/analytics/linea-en-vivo/). Además, ScanRequest.unidades_procesadas permite
que el emisor (Node-RED) mande el conteo ya resuelto cuando el tamaño de
lote depende de una señal del propio PLC y no del SKU nominalmente activo.
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import (
    Estacion, EstadoOrden, Linea, MaestroSKU, OrdenProduccion, Planta,
    TipoProduccion,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id, tipo_produccion=TipoProduccion.POR_LOTES):
    planta = Planta(tenant_id=tenant_id, nombre="Planta P")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea P", tipo_produccion=tipo_produccion)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(
        tenant_id=tenant_id, nombre="Armadora", tipo="sensor", linea_id=linea.id,
        umbral_optimo=10, umbral_lento=15, umbral_alerta=25, activa=True,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    return planta, linea, estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _crear_orden_en_progreso(db, tenant_id, linea_id, unidades_por_ciclo=4):
    sku = MaestroSKU(
        tenant_id=tenant_id, codigo_sku=f"PAN-{uuid.uuid4().hex[:8]}",
        descripcion="Pan de molde", unidades_por_ciclo=unidades_por_ciclo,
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


def test_plc_ciego_hereda_sku_y_multiplicador_de_la_orden_en_progreso(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.POR_LOTES)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, unidades_por_ciclo=5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},  # sin codigo_pieza: PLC ciego
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    assert r.json()["unidades"] == 5  # unidades_por_ciclo del SKU de la orden activa, no 1


def test_plc_ciego_sin_orden_en_progreso_no_rompe_y_cuenta_1(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.POR_LOTES)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    assert r.json()["unidades"] == 1  # sin orden activa -> nunca se inventa un multiplicador


def test_linea_discreta_ignora_multiplicador_del_sku_aunque_haya_orden_activa(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.DISCRETA)
    _crear_orden_en_progreso(db, tenant_a, linea.id, unidades_por_ciclo=5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    assert r.json()["unidades"] == 1  # discreta: 1 ping = 1 unidad, el factor de lote no aplica


def test_unidades_procesadas_explicito_pisa_la_resolucion_por_sku(client, db, tenant_a, gerente_a):
    """El canal PLC que basculó ya le dice al emisor cuántas unidades son
    (ej. 4 panes por el canal de Salida 1) -- eso manda, sin importar qué
    diga unidades_por_ciclo del SKU nominalmente activo."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.POR_LOTES)
    _crear_orden_en_progreso(db, tenant_a, linea.id, unidades_por_ciclo=5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "unidades_procesadas": 4},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    assert r.json()["unidades"] == 4  # no 5: el conteo edge-autoritativo pisa al del SKU


def test_unidades_procesadas_mismo_event_id_mismo_valor_es_idempotente(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.POR_LOTES)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    event_id = str(uuid.uuid4())
    payload = {"event_id": event_id, "id_estacion": str(estacion.id), "unidades_procesadas": 4}

    r1 = client.post("/api/lite/scans", json=payload, headers={"X-Device-Key": credencial})
    assert r1.status_code == 201

    r2 = client.post("/api/lite/scans", json=payload, headers={"X-Device-Key": credencial})
    assert r2.status_code == 200
    assert r2.json()["idempotente"] is True
    assert r2.json()["unidades"] == 4


def test_unidades_procesadas_mismo_event_id_distinto_valor_es_conflicto(client, db, tenant_a, gerente_a):
    """El hash de idempotencia incluye unidades_procesadas: si dos POSTs con
    el mismo event_id traen un conteo distinto, es un payload distinto de
    verdad -- 409, no se acepta el primero silenciosamente."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.POR_LOTES)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    event_id = str(uuid.uuid4())

    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": event_id, "id_estacion": str(estacion.id), "unidades_procesadas": 4},
        headers={"X-Device-Key": credencial},
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": event_id, "id_estacion": str(estacion.id), "unidades_procesadas": 5},
        headers={"X-Device-Key": credencial},
    )
    assert r2.status_code == 409


def test_codigo_pieza_regex_sigue_teniendo_prioridad_sobre_la_orden_en_progreso(client, db, tenant_a, gerente_a):
    """Regresión: Springwall (escaneo con codigo_pieza) no se tiene que ver
    afectado por el fallback nuevo -- si el regex resuelve una orden, esa
    gana, no la EN_PROGRESO de la línea."""
    from app.models.domain import Tenant

    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.DISCRETA)
    tenant = db.get(Tenant, tenant_a)
    tenant.regex_parser_orden = r"^(OP\d+)-"
    db.add(tenant)
    db.commit()

    _crear_orden_en_progreso(db, tenant_a, linea.id, unidades_por_ciclo=1)  # orden EN_PROGRESO distinta
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/scans",
        json={
            "event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id),
            "codigo_pieza": "OP9999-MOD1-00042",
        },
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201

    from app.models.domain import LiteEventoProduccion
    from sqlmodel import select
    evento = db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id))).first()
    assert evento.orden_fk == "OP9999"  # del regex, no de la orden EN_PROGRESO

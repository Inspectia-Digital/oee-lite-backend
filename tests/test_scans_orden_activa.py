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

    # Fase AC: el perfil de tiempos vive en Línea (Estación ya no tiene
    # uno propio) -- se setea acá en vez de en Linea(...) de arriba para
    # tocar el mínimo del resto de este archivo.
    linea.tiempo_ideal_seg, linea.tiempo_lento_seg, linea.tiempo_alerta_seg = 10, 15, 25
    db.add(linea)
    db.commit()

    estacion = Estacion(
        tenant_id=tenant_id, nombre="Armadora", tipo="sensor", linea_id=linea.id, activa=True,
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


def test_por_lotes_umbral_de_parada_es_por_ciclo_no_por_unidad(client, db, tenant_a, gerente_a):
    """Bug real encontrado al preparar la carga de Green Mills: delta_t
    (tiempo entre EVENTOS/bandejas consecutivas) se comparaba contra un
    umbral derivado de tiempo_ideal_seg SIN escalar por unidades_por_ciclo
    -- ese campo es "segundos ideales POR UNIDAD" (ver
    MaestroSKU.tiempo_ideal_seg), así que compararlo tal cual contra
    delta_t (que mide tiempo por CICLO) marcaba cada bandeja normal como
    una parada grande. Nunca se detectó porque sku_final nunca se
    resolvía antes de este fix (Fase P, sección 1.b) -- esta rama jamás
    había corrido con un SKU real."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.POR_LOTES)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, unidades_por_ciclo=5)
    # seg/unidad -> ciclo ideal de 5 unidades = 10s (lento/alerta con el
    # mismo multiplicador 1.15/1.25 que antes era el default del tenant).
    sku.tiempo_ideal_seg = 2.0
    sku.tiempo_lento_seg = 2.3
    sku.tiempo_alerta_seg = 2.5
    db.add(sku)
    db.commit()
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc)
    # 4 bandejas normales, exactamente cada 10s (el ciclo ideal calculado).
    for i in range(4):
        ts = (ahora + timedelta(seconds=10 * i)).isoformat()
        r = client.post(
            "/api/lite/scans",
            json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": ts},
            headers={"X-Device-Key": credencial},
        )
        assert r.status_code == 201

    # 5to evento: hueco real de 100s (una parada de verdad).
    ts_parada = (ahora + timedelta(seconds=30 + 100)).isoformat()
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": ts_parada},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201

    from app.models.domain import LiteEventoProduccion, ParadaDetectada
    from sqlmodel import select

    eventos = db.exec(
        select(LiteEventoProduccion)
        .where(LiteEventoProduccion.id_estacion == str(estacion.id))
        .order_by(LiteEventoProduccion.timestamp)
    ).all()
    assert len(eventos) == 5
    # Las primeras 4 bandejas (delta_t=10s contra un ciclo ideal de 10s) NO
    # deben clasificar ALERTA -- con el bug, 10s contra un umbral "por
    # unidad" de ~2.5s (2.0 * 1.25) las hubiese marcado ALERTA a todas.
    assert [e.estado for e in eventos[:4]] == ["OPTIMO", "OPTIMO", "OPTIMO", "OPTIMO"]
    # El hueco real de 100s sí debe detectarse.
    assert eventos[4].estado == "ALERTA"

    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 1
    # t_alerta = tiempo_alerta_seg del SKU(2.5) * unidades_por_ciclo(5) = 12.5
    # tiempo_perdido = delta_t(100) - t_alerta(12.5) = 87.5
    assert paradas[0].duracion_segundos == 87.5


def test_cambio_de_orden_activa_no_genera_parada_aunque_el_hueco_sea_grande(client, db, tenant_a, gerente_a):
    """Fase Q (feedback de producto, onboarding Green Mills): una orden
    tiene día/turno de INICIO pero no de fin definido -- puede seguir
    corriendo más allá de su turno nominal (horas extra, cambio de turno
    sin cortar producción). El límite real de "sigue siendo la misma
    sesión de producción" es la continuidad de la ORDEN activa, no el
    reloj. Si la orden cambió entre dos eventos consecutivos (la anterior
    se cerró, arrancó una distinta), el hueco entre ellos es un límite de
    sesión, no una parada -- aunque sea de horas."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.DISCRETA)
    orden1, _ = _crear_orden_en_progreso(db, tenant_a, linea.id)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc)
    hace_2_horas = ahora - timedelta(hours=2)
    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": hace_2_horas.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r1.status_code == 201

    # La orden 1 se cierra, arranca la orden 2 -- cambio de sesión real.
    orden1.estado = EstadoOrden.CERRADA
    db.add(orden1)
    db.commit()
    orden2, _ = _crear_orden_en_progreso(db, tenant_a, linea.id)

    # Hueco de 2 horas -- de sobra para cruzar cualquier perfil de tiempos razonable.
    # El evento 2 va "ahora" (el 1 quedó hace 2h): /api/lite/scans rechaza
    # timestamps en el futuro, por eso el hueco se arma hacia atrás.
    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": ahora.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r2.status_code == 201

    from app.models.domain import LiteEventoProduccion, ParadaDetectada
    from sqlmodel import select

    eventos = db.exec(
        select(LiteEventoProduccion)
        .where(LiteEventoProduccion.id_estacion == str(estacion.id))
        .order_by(LiteEventoProduccion.timestamp)
    ).all()
    assert len(eventos) == 2
    assert eventos[0].orden_fk == orden1.id_orden
    assert eventos[1].orden_fk == orden2.id_orden
    assert eventos[1].estado == "OPTIMO"  # no ALERTA pese al hueco de 2h: cambió la orden

    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 0  # el cambio de orden no genera parada


def test_misma_orden_activa_con_hueco_grande_si_genera_parada(client, db, tenant_a, gerente_a):
    """Contraparte del test anterior: si la orden activa NO cambió, un
    hueco real se sigue evaluando normal contra los umbrales -- aunque
    cruce lo que sería un límite de turno nominal, sigue siendo la misma
    sesión de producción (regla de negocio: una orden puede seguir
    corriendo más allá de su turno de inicio)."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a, TipoProduccion.DISCRETA)
    orden, _ = _crear_orden_en_progreso(db, tenant_a, linea.id)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc)
    hace_2_horas = ahora - timedelta(hours=2)
    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": hace_2_horas.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r1.status_code == 201

    # Misma orden sigue en progreso -- hueco real de 2 horas, sí es una
    # parada real. Evento 2 "ahora" (el 1 quedó hace 2h): /api/lite/scans
    # rechaza timestamps en el futuro, por eso el hueco se arma hacia atrás.
    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": ahora.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r2.status_code == 201

    from app.models.domain import LiteEventoProduccion, ParadaDetectada
    from sqlmodel import select

    eventos = db.exec(
        select(LiteEventoProduccion)
        .where(LiteEventoProduccion.id_estacion == str(estacion.id))
        .order_by(LiteEventoProduccion.timestamp)
    ).all()
    assert eventos[0].orden_fk == eventos[1].orden_fk == orden.id_orden
    assert eventos[1].estado == "ALERTA"

    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 1

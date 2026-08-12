"""Fase AF (pedido de Green Mills, "análisis de paradas por SKU"):
ParadaDetectada.orden_fk (nuevo) + /analytics/paradas-por-sku/.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import (
    Estacion, EstadoOrden, Linea, MaestroSKU, MotivoParada, OrdenProduccion,
    ParadaDetectada, Planta, TipoParada,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AF")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea AF", tiempo_ideal_seg=2, tiempo_lento_seg=3, tiempo_alerta_seg=5)
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación AF", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def _crear_orden_en_progreso(db, tenant_id, linea_id, codigo_sku):
    sku = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo_sku, MaestroSKU.tenant_id == tenant_id)).first()
    if sku is None:
        sku = MaestroSKU(tenant_id=tenant_id, codigo_sku=codigo_sku, descripcion=f"SKU {codigo_sku}", tiempo_ideal_seg=2, tiempo_lento_seg=3, tiempo_alerta_seg=5)
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


def test_parada_automatica_graba_orden_fk_de_la_orden_activa(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden = _crear_orden_en_progreso(db, tenant_a, linea.id, f"SKU-AF-{uuid.uuid4().hex[:6]}")
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc)
    _postear_evento(client, credencial, estacion.id, ahora)
    # 20s > tiempo_alerta_seg del SKU (5, perfil completo armado en el helper) -> ALERTA.
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=20))

    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 1
    assert paradas[0].orden_fk == orden.id


def test_parada_automatica_sin_orden_activa_queda_orden_fk_null(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc)
    _postear_evento(client, credencial, estacion.id, ahora)
    # 20s > tiempo_alerta_seg de línea (5) -> ALERTA, sin ninguna orden resuelta.
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=20))

    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 1
    assert paradas[0].orden_fk is None


def test_paradas_por_sku_agrupa_por_sku_y_motivo(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    codigo_sku = f"SKU-AF2-{uuid.uuid4().hex[:6]}"
    _crear_orden_en_progreso(db, tenant_a, linea.id, codigo_sku)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    _postear_evento(client, credencial, estacion.id, ahora)
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=20))  # ALERTA -> parada con SKU

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/paradas-por-sku/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["sku"] == codigo_sku
    assert filas[0]["motivo"] == "Sin Clasificar (Pendiente)"
    assert filas[0]["frecuencia"] == 1


def test_paradas_por_sku_sin_orden_asociada_cae_en_sin_sku(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    _postear_evento(client, credencial, estacion.id, ahora)
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=20))

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/paradas-por-sku/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["sku"] == "Sin SKU asociado"


def test_paradas_por_sku_clasificada_muestra_su_motivo_real(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    codigo_sku = f"SKU-AF3-{uuid.uuid4().hex[:6]}"
    _crear_orden_en_progreso(db, tenant_a, linea.id, codigo_sku)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    _postear_evento(client, credencial, estacion.id, ahora)
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=20))

    parada = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).first()
    motivo = MotivoParada(tenant_id=tenant_a, nombre="Falla mecánica", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)

    autenticar_como(gerente_a.id)
    r = client.patch(
        f"/supervisor/paradas/{parada.id}/clasificar",
        json={"motivo_fk": str(motivo.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200

    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/paradas-por-sku/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["sku"] == codigo_sku
    assert filas[0]["motivo"] == "Falla mecánica"
    assert filas[0]["tipo"] == "NO_PLANIFICADA"

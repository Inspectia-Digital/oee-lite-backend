"""Fase DS (auditoría de backend, P0-03): Calidad por tiempo resuelve
MaestroSKU.umbral_calidad del SKU real que corría en cada evento --
antes usaba siempre UMBRAL_CALIDAD_POR_TIEMPO_FALLBACK_SEG (1800s) fijo,
sin importar qué SKU estuviera activo."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import (
    Estacion, EstadoOrden, Linea, MaestroSKU, MetodoCalidadLinea,
    OrdenProduccion, Planta, Turno,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta DS")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    # tiempo_alerta_seg alto a propósito: el delta de 45s de estos tests
    # no debe disparar la rama ALERTA de scans.py (que capea
    # delta_t_segundos a tiempo_ideal_por_ciclo) -- necesitamos el delta
    # real sin capear para ejercitar el umbral de calidad por tiempo.
    linea = Linea(
        tenant_id=tenant_id, planta_id=planta.id, nombre="Línea DS",
        metodo_calidad=MetodoCalidadLinea.POR_TIEMPO,
        tiempo_ideal_seg=10, tiempo_lento_seg=20, tiempo_alerta_seg=300,
    )
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(
        tenant_id=tenant_id, nombre="Inspección DS", tipo="calidad", linea_id=linea.id, activa=True,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_id, nombre="Full", hora_inicio="00:00:00", hora_fin="23:59:00", linea_id=linea.id)
    db.add(turno)
    db.commit()

    return planta, linea, estacion


def _crear_orden_en_progreso(db, tenant_id, linea_id, umbral_calidad):
    sku = MaestroSKU(
        tenant_id=tenant_id, codigo_sku=f"SKU-DS-{uuid.uuid4().hex[:8]}",
        descripcion="SKU calidad por tiempo", umbral_calidad=umbral_calidad,
    )
    db.add(sku)
    db.commit()

    orden = OrdenProduccion(
        tenant_id=tenant_id, id_orden=f"OP-DS-{uuid.uuid4().hex[:8]}", linea_id=linea_id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=sku.codigo_sku,
    )
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden, sku


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _dos_scans_con_delta(client, credencial, estacion_id, delta_segundos):
    ahora = datetime.now(timezone.utc)
    primero = ahora - timedelta(seconds=delta_segundos)
    for ts in (primero, ahora):
        r = client.post(
            "/api/lite/scans",
            json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id), "timestamp": ts.isoformat()},
            headers={"X-Device-Key": credencial},
        )
        assert r.status_code == 201


def test_calidad_por_tiempo_usa_umbral_bajo_del_sku_y_reprueba(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _crear_orden_en_progreso(db, tenant_a, linea.id, umbral_calidad=30.0)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    _dos_scans_con_delta(client, credencial, estacion.id, delta_segundos=45)

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    # 2 eventos entran a la ventana: el primero (sin evento previo en la
    # estación) siempre tiene delta_t_segundos=0 -> aprueba solo; el
    # segundo tiene delta 45s > umbral 30s del SKU -> no aprueba.
    # 1 de 2 unidades buenas -> calidad 50%.
    assert r.json()["calidad_pct"] == 50.0


def test_calidad_por_tiempo_usa_umbral_alto_del_sku_y_aprueba(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _crear_orden_en_progreso(db, tenant_a, linea.id, umbral_calidad=60.0)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    # Mismo delta (45s) que el test anterior -- el SKU distinto (umbral
    # 60s en vez de 30s) es lo único que cambia el resultado.
    _dos_scans_con_delta(client, credencial, estacion.id, delta_segundos=45)

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert r.json()["calidad_pct"] == 100.0


def test_calidad_por_tiempo_sin_orden_resoluble_usa_fallback_fijo(client, db, tenant_a, gerente_a):
    """PLC ciego sin ninguna OrdenProduccion EN_PROGRESO en la línea:
    orden_fk queda None -- no hay SKU que resolver, cae al fallback fijo
    (1800s). Con un delta chico (45s) siempre aprueba contra ese fallback."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    _dos_scans_con_delta(client, credencial, estacion.id, delta_segundos=45)

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert r.json()["calidad_pct"] == 100.0

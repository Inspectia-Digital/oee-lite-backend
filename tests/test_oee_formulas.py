"""Fase E2: motor OEE (rendimiento, calidad, fallback N/A, incluido_oee)."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import Estacion, Linea, Planta, Turno, MetodoCalidadLinea
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id, metodo_calidad=MetodoCalidadLinea.POR_RECHAZO):
    planta = Planta(tenant_id=tenant_id, nombre="Planta OEE")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea OEE", metodo_calidad=metodo_calidad)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(
        tenant_id=tenant_id, nombre="Estación OEE", tipo="sensor", linea_id=linea.id,
        umbral_optimo=100, umbral_lento=150, umbral_alerta=200, activa=True,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_id, nombre="Full", hora_inicio="00:00:00", hora_fin="23:59:00", linea_id=linea.id)
    db.add(turno)
    db.commit()

    return planta, linea, estacion


def _emitir_key_y_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def test_calidad_por_rechazo_calcula_correctamente(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a, MetodoCalidadLinea.POR_RECHAZO)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc)
    for i, rechazadas in enumerate([0, 0, 1]):
        ts = (ahora - timedelta(minutes=10 * (2 - i))).isoformat()
        r = client.post(
            "/api/lite/scans",
            json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": ts, "unidades_rechazadas": rechazadas},
            headers={"X-Device-Key": credencial},
        )
        assert r.status_code == 201

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    data = r.json()
    # 3 unidades procesadas, 1 rechazada -> calidad = 2/3 = 66.7%
    assert data["calidad_pct"] == 66.7


def test_calidad_na_cuando_no_hay_datos_del_metodo_configurado(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a, MetodoCalidadLinea.POR_TIEMPO)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)

    # Estación tipo "sensor", no "calidad" -> no genera inspecciones de calidad por tiempo.
    r = client.post("/api/lite/scans", json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)}, headers={"X-Device-Key": credencial})
    assert r.status_code == 201

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    data = r.json()
    assert data["calidad_pct"] is None
    # OEE excluye el factor Calidad del producto, no lo pone en 0.
    assert data["oee_general_pct"] > 0


def test_evento_incluido_oee_false_no_suma_al_total(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)

    r1 = client.post("/api/lite/scans", json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)}, headers={"X-Device-Key": credencial})
    assert r1.status_code == 201

    autenticar_como(gerente_a.id)
    antes = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)}).json()["total_unidades"]

    estacion.activa = False
    db.add(estacion)
    db.commit()

    r2 = client.post("/api/lite/scans", json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)}, headers={"X-Device-Key": credencial})
    assert r2.status_code == 201

    despues = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)}).json()["total_unidades"]
    assert despues == antes


def test_sin_planta_seleccionada_devuelve_tarjeta_vacia_no_error(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-general/")
    assert r.status_code == 200
    assert r.json()["calidad_pct"] is None


def test_tiempo_planificado_escala_por_dias_con_produccion_no_por_rango_consultado(client, db, tenant_a, gerente_a):
    """Feedback de producto (Fase Q, onboarding Green Mills): antes
    tiempo_planificado se multiplicaba por TODO el rango consultado
    (dias_consulta) -- pedir "últimos 7 días" con producción real en un
    solo día diluía Disponibilidad/Rendimiento contra 6 días que nunca
    tuvieron un turno corriendo (se vio en vivo: 3 capturas cortas de
    prueba del PLC, cargadas en un solo día, dieron Rendimiento ~0.8%
    contra un denominador de 7 días de turno completo). Ahora
    tiempo_planificado escala por los días CALENDARIO (hora de planta)
    que efectivamente tuvieron al menos un evento -- tiempo_calendario
    sigue reflejando el rango completo pedido, esa sí es una medida del
    rango, no de cuánto se produjo en él."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)

    hoy = datetime.now(timezone.utc)
    hace_5_dias = hoy - timedelta(days=5)

    # Un solo evento, hace 5 días -- el único día con producción real
    # dentro del rango de 7 días que se va a consultar.
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": hace_5_dias.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/oee-cascada/",
        params={
            "fecha_desde": (hoy - timedelta(days=6)).date().isoformat(),
            "fecha_hasta": hoy.date().isoformat(),
        },
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    data = r.json()
    # Rango consultado: 7 días -> tiempo_calendario NO cambia, sigue
    # siendo el rango completo pedido (7 * 24h = 10080 min).
    assert data["tiempo_calendario_min"] == 10080.0
    # Turno de _preparar_escenario: 00:00-23:59, sin descanso -> 1439
    # min/día. Con el fix, tiempo_planificado = 1 día de producción real
    # (no 7): 1439.0 min, no 1439*7=10073.0.
    assert data["tiempo_planificado_min"] == 1439.0

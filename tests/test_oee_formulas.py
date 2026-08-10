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


def test_tiempo_planificado_no_escala_por_rango_consultado_ni_por_turno_nominal(client, db, tenant_a, gerente_a):
    """Feedback de producto (Fase Q, onboarding Green Mills), en dos rondas:

    Ronda 1: tiempo_planificado se multiplicaba por TODO el rango
    consultado (dias_consulta) -- pedir "últimos 7 días" con producción
    real en un solo día diluía Disponibilidad/Rendimiento contra 6 días
    que nunca tuvieron un turno corriendo.

    Ronda 2 (este test, actualizado): ni siquiera alcanzaba con contar
    sólo los días CON producción -- seguía multiplicando por la duración
    NOMINAL del turno completo (Turno.hora_inicio/fin), así que 1 sólo
    evento de prueba (~100s ideales) se seguía comparando contra un
    turno nominal de horas. Ahora tiempo_planificado se arma de abajo
    hacia arriba, evento por evento (tiempo ideal + lentitud + paradas,
    ver _calcular_metricas_oee) -- no depende de dias_consulta NI de la
    duración del turno configurado. tiempo_calendario sigue reflejando
    el rango completo pedido, esa sí es una medida del rango en sí."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)  # umbral_optimo=100 (sin SKU/orden)
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
    # 1 evento, sin ultimo_evento previo -> no genera delta_t/lentitud/
    # parada. tiempo_planificado = tiempo_ideal del único evento =
    # umbral_optimo(100s) * unidades(1) = 100s = 1.6666min -> 1.7. Nada
    # que ver con el turno nominal (00:00-23:59 = 1439 min/día) ni con
    # los 7 días del rango.
    assert data["tiempo_planificado_min"] == 1.7


def test_lentitud_entre_optimo_y_alerta_suma_a_minutos_perdidos_y_baja_rendimiento(client, db, tenant_a, gerente_a):
    """Fase Q (feedback de producto, ronda 2): antes sólo se detectaba/
    guardaba el excedente cuando un evento cruzaba a ALERTA
    (ParadaDetectada). Los eventos LENTO (entre umbral_optimo y
    umbral_alerta) quedaban marcados en el evento individual pero nunca
    se sumaban a nada -- la card "Minutos Perdidos" siempre daba 0
    (minutos_desvio_calidad estaba directamente hardcodeado a 0.0).
    Ahora tiempo_planificado se arma de abajo hacia arriba: ideal +
    lentitud + paradas -- y Rendimiento/Disponibilidad/Minutos Perdidos
    salen de ahí, no de una duración de turno nominal."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)  # umbral_optimo=100, lento=150, alerta=200
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc)
    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": ahora.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r1.status_code == 201

    # 170s: > umbral_lento(150), <= umbral_alerta(200) -> LENTO, no ALERTA.
    ts2 = (ahora + timedelta(seconds=170)).isoformat()
    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": ts2},
        headers={"X-Device-Key": credencial},
    )
    assert r2.status_code == 201

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    data = r.json()

    # tiempo_ideal_total = 2 eventos * 100s (umbral_optimo, sin SKU) = 200s.
    # tiempo_perdido_lentitud = delta_t(170) - tiempo_ideal_evento(100) = 70s.
    # tiempo_planificado = 200 + 70 + 0(paradas) = 270s.
    # Rendimiento = 200/270 = 74.07...% -> 74.1%.
    assert data["rendimiento_pct"] == 74.1
    # Sin paradas (no cruzó ALERTA) -> Disponibilidad 100%.
    assert data["disponibilidad_pct"] == 100.0
    # Minutos Perdidos = lentitud(70s) + paradas no planificadas(0) = 70s = 1.1666min -> 1.2.
    assert data["minutos_desvio_calidad"] == 1.2

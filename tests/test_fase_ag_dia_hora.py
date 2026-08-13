"""Fase AG (pedido de Green Mills, "día particular / día promedio / por
horas"): /analytics/dia-detalle/ y /analytics/dia-promedio/. Agrupado
siempre por HORA LOCAL DE PLANTA (_hora_planta), nunca UTC crudo -- mismo
mecanismo ya probado en Fase AD (turno por horario).

Decisión de diseño para dia-promedio (planteada como pregunta abierta en
el documento de diseño, sin corrección del usuario -- se sigue el
default declarado ahí): las cantidades se dividen por los días del rango
que efectivamente tuvieron producción, no por el total de días del rango.
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import Estacion, Linea, Planta
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    # UTC-3 fijo (sin DST desde 2009) -- un evento a las 14:00 UTC cae
    # siempre a las 11:00 hora de planta, para cualquier fecha.
    planta = Planta(tenant_id=tenant_id, nombre="Planta AG", timezone="America/Argentina/Buenos_Aires")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea AG", tiempo_ideal_seg=5, tiempo_lento_seg=500, tiempo_alerta_seg=1000)
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación AG", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def _dia_reciente_a_las_14utc(dias_atras: int) -> datetime:
    """/api/lite/scans rechaza timestamps de más de 7 días de antigüedad
    (_validar_rango_timestamp) -- a diferencia de otros tests de esta
    sesión (Fase AD) que usan datetime.now() +/- horas, acá hace falta un
    DÍA CALENDARIO controlado (para dia-detalle/dia-promedio), así que se
    ancla a "hoy - N días" en vez de una fecha fija, manteniéndose
    siempre dentro de la ventana de 7 días."""
    return (datetime.now(timezone.utc) - timedelta(days=dias_atras)).replace(hour=14, minute=0, second=0, microsecond=0)


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


def test_dia_detalle_agrupa_en_hora_local_de_planta(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ts = _dia_reciente_a_las_14utc(2)  # 11:00 hora de planta
    dia = ts.date()
    _postear_evento(client, credencial, estacion.id, ts)
    _postear_evento(client, credencial, estacion.id, ts + timedelta(seconds=5))

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/dia-detalle/",
        params={"fecha": dia.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 24  # siempre las 24 horas, incluso en cero -- sin huecos para el gráfico.
    por_hora = {f["hora"]: f for f in filas}
    assert por_hora[11]["unidades_producidas"] == 2
    assert por_hora[11]["rendimiento_pct"] == 100.0
    assert all(por_hora[h]["unidades_producidas"] == 0 for h in range(24) if h != 11)


def test_dia_detalle_hora_con_lentitud_baja_rendimiento(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ts = _dia_reciente_a_las_14utc(2)  # 11:00 hora de planta
    _postear_evento(client, credencial, estacion.id, ts)
    # delta=520s > tiempo_lento_seg(500), < tiempo_alerta_seg(1000) -> LENTO.
    _postear_evento(client, credencial, estacion.id, ts + timedelta(seconds=520))

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/dia-detalle/",
        params={"fecha": ts.date().isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    por_hora = {f["hora"]: f for f in r.json()}
    # ideal_total=5+5=10; lentitud=520-5=515 -> rendimiento=10/525*100=1.9.
    assert por_hora[11]["rendimiento_pct"] == 1.9
    assert por_hora[11]["minutos_perdidos"] > 0


def test_dia_promedio_divide_por_dias_con_actividad_no_por_todo_el_rango(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    # Rango de 5 días, pero sólo 2 tienen producción real -- el promedio
    # de esa hora tiene que dividir por 2 días con actividad, no por los
    # 5 del rango completo.
    base = _dia_reciente_a_las_14utc(5)  # 11:00 hora de planta
    _postear_evento(client, credencial, estacion.id, base)
    _postear_evento(client, credencial, estacion.id, base + timedelta(seconds=5))
    _postear_evento(client, credencial, estacion.id, base + timedelta(days=1))
    _postear_evento(client, credencial, estacion.id, base + timedelta(days=1, seconds=5))

    autenticar_como(gerente_a.id)
    fecha_desde = base.date()
    fecha_hasta = base.date() + timedelta(days=4)
    r = client.get(
        "/analytics/dia-promedio/",
        params={"fecha_desde": fecha_desde.isoformat(), "fecha_hasta": fecha_hasta.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    por_hora = {f["hora"]: f for f in r.json()}
    # 4 unidades totales en la hora 11 / 2 días con actividad = 2 (no 4/5=0.8).
    assert por_hora[11]["unidades_promedio"] == 2.0


def test_dia_promedio_sin_datos_no_falla(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/dia-promedio/",
        params={"fecha_desde": (hoy - timedelta(days=30)).isoformat(), "fecha_hasta": (hoy - timedelta(days=26)).isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 24
    assert all(f["unidades_promedio"] == 0.0 for f in filas)


def test_dia_promedio_rango_invertido_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/dia-promedio/",
        params={"fecha_desde": hoy.isoformat(), "fecha_hasta": (hoy - timedelta(days=4)).isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400

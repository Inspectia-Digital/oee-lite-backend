"""Fase V: /analytics/cuellos-botella/ y /analytics/rendimiento-secuencial/
usaban una metodología de "desvío"/"performance" que no coincidía con el
Rendimiento agregado (_calcular_metricas_oee) ni siquiera para una línea
con una sola estación -- el agregado es asimétrico (sólo cuenta el
excedente de eventos LENTO sobre su tiempo ideal, un evento más rápido
que lo ideal no "bonifica" nada) mientras el desagregado comparaba
promedios crudos de forma simétrica, pudiendo irse a valores absurdos
(desvíos negativos o de +700%) sin relación con el Rendimiento mostrado
arriba en la misma pantalla.

Criterio de éxito explícito del usuario: con una sola estación en la
línea, el desagregado (Cuellos de Botella / Performance por Estación)
tiene que dar EXACTAMENTE lo mismo que el agregado -- es un desagregado
del mismo número, no un cálculo paralelo. Con varias estaciones, la suma
de los desagregados por estación tiene que reconstruir el total agregado
(es una partición del mismo conjunto de eventos, no una aproximación).
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import Estacion, Linea, Planta
from tests.conftest import autenticar_como


def _crear_planta_linea_estacion(db, tenant_id, nombre_sufijo, posicion_linea=1, **kwargs_estacion):
    planta = Planta(tenant_id=tenant_id, nombre=f"Planta V{nombre_sufijo}")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre=f"Línea V{nombre_sufijo}")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(
        tenant_id=tenant_id, nombre=f"Estación V{nombre_sufijo}", tipo="sensor", linea_id=linea.id,
        activa=True, posicion_linea=posicion_linea,
        umbral_optimo=100, umbral_lento=150, umbral_alerta=200,
        **kwargs_estacion,
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


def _postear_evento(client, credencial, estacion_id, ts):
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id), "timestamp": ts.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    return r


def _cargar_secuencia_estandar(client, credencial, estacion_id, inicio):
    """3 eventos: 1ro OPTIMO (delta=0), 2do LENTO (delta=170s, entre
    umbral_lento=150 y umbral_alerta=200), 3ro ALERTA (delta=250s, > 200).
    Con tiempo_ideal_seg=100 (umbral_optimo) en los tres:
    ideal_total=300, lentitud=170-100=70 -> rendimiento=300/370=81.081...%
    -> 81.1; desvio_pct=70/300*100=23.333...->23.3."""
    ts1 = inicio
    ts2 = ts1 + timedelta(seconds=170)
    ts3 = ts2 + timedelta(seconds=250)
    for ts in (ts1, ts2, ts3):
        _postear_evento(client, credencial, estacion_id, ts)


def test_una_sola_estacion_cuellos_botella_y_secuencial_coinciden_exacto_con_agregado(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _crear_planta_linea_estacion(db, tenant_a, "1")
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    _cargar_secuencia_estandar(client, credencial, estacion.id, ahora)

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    params = {
        "fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(),
        "linea_id": str(linea.id),
    }
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    agregado = client.get("/analytics/oee-general/", params=params, headers=headers)
    assert agregado.status_code == 200
    rendimiento_agregado_pct = agregado.json()["rendimiento_pct"]
    assert rendimiento_agregado_pct == 81.1  # 300 / (300+70) * 100

    cuellos = client.get("/analytics/cuellos-botella/", params=params, headers=headers)
    assert cuellos.status_code == 200
    filas_cuellos = cuellos.json()
    assert len(filas_cuellos) == 1
    assert filas_cuellos[0]["desvio_pct"] == 23.3  # 70 / 300 * 100

    secuencial = client.get("/analytics/rendimiento-secuencial/", params=params, headers=headers)
    assert secuencial.status_code == 200
    filas_secuencial = secuencial.json()
    assert len(filas_secuencial) == 1

    # El criterio de éxito explícito: con una sola estación, el
    # desagregado coincide EXACTO con el agregado de arriba.
    assert filas_secuencial[0]["rendimiento_pct"] == rendimiento_agregado_pct


def test_multiples_estaciones_la_suma_de_lo_desagregado_reconstruye_el_agregado(client, db, tenant_a, gerente_a):
    """Robustez explícitamente pedida: el cálculo tiene que seguir dando
    bien al sumar estaciones, no ser sólo un caso especial de 1 sola."""
    planta = Planta(tenant_id=tenant_a, nombre="Planta V-multi")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea V-multi")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    est1 = Estacion(
        tenant_id=tenant_a, nombre="Est V-multi 1", tipo="sensor", linea_id=linea.id,
        activa=True, posicion_linea=1, umbral_optimo=100, umbral_lento=150, umbral_alerta=200,
    )
    est2 = Estacion(
        tenant_id=tenant_a, nombre="Est V-multi 2", tipo="sensor", linea_id=linea.id,
        activa=True, posicion_linea=2, umbral_optimo=50, umbral_lento=80, umbral_alerta=120,
    )
    db.add(est1)
    db.add(est2)
    db.commit()
    db.refresh(est1)
    db.refresh(est2)

    cred1 = _emitir_credencial(client, gerente_a, est1.id)
    cred2 = _emitir_credencial(client, gerente_a, est2.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    # Est 1: misma secuencia estándar -> ideal=300, lentitud=70.
    _cargar_secuencia_estandar(client, cred1, est1.id, ahora)
    # Est 2: 2 eventos, ideal=50 c/u -> ideal_total=100. 1ro OPTIMO (delta=0).
    # 2do LENTO (delta=90s > umbral_lento(80), <= umbral_alerta(120)) ->
    # lentitud=90-50=40.
    _postear_evento(client, cred2, est2.id, ahora)
    _postear_evento(client, cred2, est2.id, ahora + timedelta(seconds=90))

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    params = {
        "fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(),
        "linea_id": str(linea.id),
    }
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    agregado = client.get("/analytics/oee-general/", params=params, headers=headers).json()
    # ideal_total = 300+100=400; lentitud_total=70+40=110 -> rendimiento=400/510*100=78.43...->78.4
    assert agregado["rendimiento_pct"] == 78.4

    secuencial = client.get("/analytics/rendimiento-secuencial/", params=params, headers=headers).json()
    assert len(secuencial) == 2
    por_estacion = {f["estacion"]: f for f in secuencial}
    assert por_estacion["Est V-multi 1"]["rendimiento_pct"] == 81.1
    # ideal=100, lentitud=40 -> rendimiento = 100/140*100 = 71.428...->71.4
    assert por_estacion["Est V-multi 2"]["rendimiento_pct"] == 71.4

    cuellos = client.get("/analytics/cuellos-botella/", params=params, headers=headers).json()
    por_estacion_c = {f["estacion"]: f for f in cuellos}
    assert por_estacion_c["Est V-multi 1"]["desvio_pct"] == 23.3
    assert por_estacion_c["Est V-multi 2"]["desvio_pct"] == 40.0  # 40/100*100

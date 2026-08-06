"""Fase O -- hallazgo nuevo, no reportado por la auditoría del front:
/analytics/oee-tendencia/ devolvía una serie 100% inventada
(oee=70+i*2 hardcodeado), sin tocar la base en absoluto. También cubre el
bug real que eso destapó: _calcular_metricas_oee ignoraba fecha_hasta
por completo (usaba obtener_rango_dia(fecha_desde) solo), así que pedir
un rango de varios días en /analytics/oee-general/ siempre devolvía
apenas el primer día."""
from datetime import date, datetime, timedelta

from app.models.domain import Estacion, LiteEventoProduccion, Linea, Planta
from tests.conftest import autenticar_como


def _preparar_linea(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Tendencia")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Tendencia")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación Tendencia", tipo="sensor", linea_id=linea.id, umbral_optimo=60)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def _emitir_en_fecha(db, tenant_id, estacion_id, fecha, unidades=1):
    ts = datetime.combine(fecha, datetime.min.time()) + timedelta(hours=10)
    db.add(LiteEventoProduccion(
        tenant_id=tenant_id, id_estacion=str(estacion_id), timestamp=ts,
        unidades_procesadas=unidades, estado="OPTIMO", tiempo_ideal_seg=60.0,
        incluido_oee=True,
    ))
    db.commit()


def test_tendencia_no_devuelve_la_serie_hardcodeada(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/oee-tendencia/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 7  # default: últimos 7 días
    # La serie vieja hardcodeaba oee=round(70 + i*2, 1) para i en 5..0 ->
    # nunca 0.0. Sin eventos reales, todos los días tienen que dar 0.0.
    assert all(f["oee"] == 0.0 for f in filas)


def test_tendencia_refleja_produccion_real_de_un_dia(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_linea(db, tenant_a)
    hoy = date.today()
    _emitir_en_fecha(db, tenant_a, estacion.id, hoy, unidades=5)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/oee-tendencia/",
        params={"linea_id": str(linea.id), "fecha_desde": hoy.isoformat(), "fecha_hasta": hoy.isoformat()},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["oee"] > 0.0


def test_tendencia_rango_mayor_a_90_dias_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_linea(db, tenant_a)
    autenticar_como(gerente_a.id)
    hoy = date.today()
    r = client.get(
        "/analytics/oee-tendencia/",
        params={
            "linea_id": str(linea.id),
            "fecha_desde": (hoy - timedelta(days=200)).isoformat(),
            "fecha_hasta": hoy.isoformat(),
        },
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_oee_general_rango_multi_dia_ya_no_ignora_fecha_hasta(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_linea(db, tenant_a)
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    _emitir_en_fecha(db, tenant_a, estacion.id, ayer, unidades=3)
    _emitir_en_fecha(db, tenant_a, estacion.id, hoy, unidades=4)

    autenticar_como(gerente_a.id)
    # Antes del fix, fecha_hasta se ignoraba y esto sólo veía fecha_desde (ayer) -> 3 unidades.
    r = client.get(
        "/analytics/oee-general/",
        params={"linea_id": str(linea.id), "fecha_desde": ayer.isoformat(), "fecha_hasta": hoy.isoformat()},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["total_unidades"] == 7

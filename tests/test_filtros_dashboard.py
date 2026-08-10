"""Fase O (auditoría de producción del front, ítem #2): los filtros del
dashboard (Línea, Turno, "Plan"=orden) ahora tienen efecto real en el
backend. Antes varios endpoints sólo aceptaban `fecha` (un día) y no
`linea_id`/`orden_fk`."""
from datetime import datetime, timedelta

from app.models.domain import (
    Estacion, EstadoParada, LiteEventoProduccion, Linea, MotivoParada,
    OrdenProduccion, ParadaDetectada, Planta, TipoParada,
)
from tests.conftest import autenticar_como


def _preparar_dos_lineas(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Filtros")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea1 = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Filtros 1")
    linea2 = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Filtros 2")
    db.add(linea1)
    db.add(linea2)
    db.commit()
    db.refresh(linea1)
    db.refresh(linea2)

    est1 = Estacion(tenant_id=tenant_id, nombre="Est F1", tipo="sensor", linea_id=linea1.id, umbral_optimo=60)
    est2 = Estacion(tenant_id=tenant_id, nombre="Est F2", tipo="sensor", linea_id=linea2.id, umbral_optimo=60)
    db.add(est1)
    db.add(est2)
    db.commit()
    db.refresh(est1)
    db.refresh(est2)

    return planta, linea1, linea2, est1, est2


def test_pareto_paradas_filtra_por_linea(client, db, tenant_a, gerente_a):
    planta, linea1, linea2, est1, est2 = _preparar_dos_lineas(db, tenant_a)
    motivo = MotivoParada(tenant_id=tenant_a, nombre="Motivo F", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)

    ahora = datetime.utcnow()
    db.add(ParadaDetectada(tenant_id=tenant_a, estacion_fk=est1.id, inicio=ahora, motivo_fk=motivo.id, estado=EstadoParada.CLASIFICADA, duracion_segundos=60))
    db.add(ParadaDetectada(tenant_id=tenant_a, estacion_fk=est2.id, inicio=ahora, motivo_fk=motivo.id, estado=EstadoParada.CLASIFICADA, duracion_segundos=120))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/pareto-paradas/",
        params={"linea_id": str(linea1.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["minutos_totales"] == 1.0  # sólo los 60s de linea1


def test_cuellos_botella_filtra_por_linea(client, db, tenant_a, gerente_a):
    """Fase U: cuellos-botella excluye el primer evento de cada estación
    (sin evento anterior no hay forma de resolver continuidad de orden,
    ver _eventos_con_ciclo_real) -- acá se inserta un evento previo con
    delta_t=0 antes del evento "real" que el test quiere medir, igual que
    lo haría /api/lite/scans en la práctica."""
    planta, linea1, linea2, est1, est2 = _preparar_dos_lineas(db, tenant_a)
    ahora = datetime.utcnow()
    minuto = timedelta(minutes=1)
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(est1.id), unidades_procesadas=1, delta_t_segundos=0, timestamp=ahora))
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(est1.id), unidades_procesadas=1, delta_t_segundos=90, timestamp=ahora + minuto))
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(est2.id), unidades_procesadas=1, delta_t_segundos=0, timestamp=ahora))
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(est2.id), unidades_procesadas=1, delta_t_segundos=200, timestamp=ahora + minuto))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/cuellos-botella/",
        params={"linea_id": str(linea1.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["estacion"] == "Est F1"


def test_reporte_produccion_filtra_por_orden(client, db, tenant_a, gerente_a):
    planta, linea1, linea2, est1, est2 = _preparar_dos_lineas(db, tenant_a)
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(est1.id), unidades_procesadas=4, orden_fk="OP-FILTRO-1"))
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(est1.id), unidades_procesadas=9, orden_fk="OP-FILTRO-2"))
    db.commit()

    autenticar_como(gerente_a.id)
    hoy = datetime.utcnow().date().isoformat()  # Fase P: alinear con timestamp UTC de LiteEventoProduccion
    r = client.get(
        "/analytics/reporte-produccion/",
        params={"fecha_desde": hoy, "fecha_hasta": hoy, "orden_fk": "OP-FILTRO-1"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["total_piezas"] == 4


def test_oee_general_filtra_por_orden(client, db, tenant_a, gerente_a):
    planta, linea1, linea2, est1, est2 = _preparar_dos_lineas(db, tenant_a)
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(est1.id), unidades_procesadas=5, orden_fk="OP-OEE-1", tiempo_ideal_seg=60.0, incluido_oee=True))
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(est1.id), unidades_procesadas=20, orden_fk="OP-OEE-2", tiempo_ideal_seg=60.0, incluido_oee=True))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/oee-general/",
        params={"orden_fk": "OP-OEE-1"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["total_unidades"] == 5

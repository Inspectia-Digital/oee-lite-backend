"""Fase DQ (auditoría de backend, P1-04): registrar_parada_planificada
valida que `inicio` sea futuro y que no se superponga con otra parada
planificada ya existente en la misma estación -- antes sólo validaba
que `fin > inicio` (duración positiva), sin ninguna de las dos."""
from datetime import datetime, timedelta

from app.models.domain import Estacion, Linea, MotivoParada, Planta, TipoParada
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta DQ")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea DQ")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación DQ", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    motivo = MotivoParada(tenant_id=tenant_id, nombre="Mantenimiento", tipo_parada=TipoParada.PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)

    return planta, linea, estacion, motivo


def _crear_planificada(client, planta, estacion, motivo, inicio, fin):
    return client.post(
        "/supervisor/paradas/planificadas",
        json={
            "estacion_fk": str(estacion.id),
            "motivo_fk": str(motivo.id),
            "inicio": inicio.isoformat(),
            "fin": fin.isoformat(),
        },
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )


def test_inicio_futuro_devuelve_200(client, db, tenant_a, gerente_a):
    planta, _, estacion, motivo = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    inicio = datetime.utcnow() + timedelta(days=1)
    r = _crear_planificada(client, planta, estacion, motivo, inicio, inicio + timedelta(hours=1))
    assert r.status_code == 200


def test_inicio_en_el_pasado_devuelve_400(client, db, tenant_a, gerente_a):
    planta, _, estacion, motivo = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    inicio = datetime.utcnow() - timedelta(hours=2)
    r = _crear_planificada(client, planta, estacion, motivo, inicio, inicio + timedelta(hours=1))
    assert r.status_code == 400


def test_solapa_con_planificada_existente_devuelve_409(client, db, tenant_a, gerente_a):
    planta, _, estacion, motivo = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    base = datetime.utcnow() + timedelta(days=1)

    primera = _crear_planificada(client, planta, estacion, motivo, base, base + timedelta(hours=2))
    assert primera.status_code == 200

    # Se superpone: empieza 1h después de la primera, dentro de su ventana.
    r = _crear_planificada(client, planta, estacion, motivo, base + timedelta(hours=1), base + timedelta(hours=3))
    assert r.status_code == 409


def test_no_solapa_con_planificada_consecutiva_devuelve_200(client, db, tenant_a, gerente_a):
    planta, _, estacion, motivo = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    base = datetime.utcnow() + timedelta(days=1)

    primera = _crear_planificada(client, planta, estacion, motivo, base, base + timedelta(hours=2))
    assert primera.status_code == 200

    # Arranca justo cuando termina la primera -- no se superpone.
    r = _crear_planificada(client, planta, estacion, motivo, base + timedelta(hours=2), base + timedelta(hours=3))
    assert r.status_code == 200


def test_solapa_en_otra_estacion_no_bloquea(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo = _preparar_escenario(db, tenant_a)
    otra_estacion = Estacion(tenant_id=tenant_a, nombre="Estación DQ 2", tipo="sensor", linea_id=linea.id)
    db.add(otra_estacion)
    db.commit()
    db.refresh(otra_estacion)

    autenticar_como(gerente_a.id)
    base = datetime.utcnow() + timedelta(days=1)

    primera = _crear_planificada(client, planta, estacion, motivo, base, base + timedelta(hours=2))
    assert primera.status_code == 200

    r = _crear_planificada(client, planta, otra_estacion, motivo, base + timedelta(hours=1), base + timedelta(hours=3))
    assert r.status_code == 200

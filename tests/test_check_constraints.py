"""Fase K (auditoría QA #6): las invariantes críticas del cálculo de OEE
ahora las impone Postgres, no sólo Pydantic. Estos tests escriben
directo contra el modelo (sin pasar por scans.py) para probar que la
base, no la app, es quien realmente lo impide."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.domain import Estacion, Linea, LiteEventoProduccion, Planta, AsignacionTurno, Operario, Turno


def _preparar_linea(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Constraints")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Constraints")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    return linea


def test_db_rechaza_unidades_rechazadas_mayor_a_procesadas(db, tenant_a):
    linea = _preparar_linea(db, tenant_a)
    estacion = Estacion(tenant_id=tenant_a, nombre="E1", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()

    evento = LiteEventoProduccion(
        tenant_id=tenant_a, id_estacion=str(estacion.id),
        unidades_procesadas=1, unidades_rechazadas=5,
    )
    db.add(evento)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_db_rechaza_delta_t_negativo(db, tenant_a):
    linea = _preparar_linea(db, tenant_a)
    estacion = Estacion(tenant_id=tenant_a, nombre="E2", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()

    evento = LiteEventoProduccion(
        tenant_id=tenant_a, id_estacion=str(estacion.id),
        unidades_procesadas=1, delta_t_segundos=-10.0,
    )
    db.add(evento)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_db_rechaza_asignacion_turno_duplicada(db, tenant_a):
    import uuid
    from datetime import date, time

    linea = _preparar_linea(db, tenant_a)
    estacion = Estacion(tenant_id=tenant_a, nombre="E3", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_a, nombre="T1", hora_inicio=time(6, 0), hora_fin=time(14, 0), linea_id=linea.id)
    db.add(turno)
    db.commit()
    db.refresh(turno)

    op1 = Operario(tenant_id=tenant_a, legajo="OPX1", nombre_completo="Uno")
    op2 = Operario(tenant_id=tenant_a, legajo="OPX2", nombre_completo="Dos")
    db.add(op1)
    db.add(op2)
    db.commit()
    db.refresh(op1)
    db.refresh(op2)

    hoy = date.today()
    db.add(AsignacionTurno(tenant_id=tenant_a, fecha=hoy, estacion_fk=estacion.id, operario_fk=op1.id, turno_fk=turno.id))
    db.commit()

    # Misma (tenant, fecha, turno, estacion) con otro operario, insertada
    # directo (sin pasar por el upsert del endpoint) -- la base la rechaza.
    db.add(AsignacionTurno(tenant_id=tenant_a, fecha=hoy, estacion_fk=estacion.id, operario_fk=op2.id, turno_fk=turno.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

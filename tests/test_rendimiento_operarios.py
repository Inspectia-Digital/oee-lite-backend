"""Fase O (auditoría de producción del front, ítem #4): GET
/analytics/rendimiento-operarios/. Antes el front ya llamaba a esta URL
pero el endpoint no existía en el backend en absoluto.

BE-P0-06 (PRD Go-Live Green Mills): la atribución operario->evento ahora
resuelve contra SesionOperario (intersección de intervalo), no contra
AsignacionTurno -- ver docstring del endpoint en analytics.py. Los tests
siguen creando AsignacionTurno (sigue existiendo, es dotación/staffing)
Y además una SesionOperario abierta, mismo par que scans.py crea en un
login real."""
from datetime import datetime, time, timedelta

from sqlmodel import select

from app.models.domain import (
    AsignacionTurno, Estacion, LiteEventoProduccion, Linea, Operario,
    Planta, SesionOperario, Turno,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Rend")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Rend")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación Rend", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_id, nombre="Full", hora_inicio=time(0, 0), hora_fin=time(23, 59), linea_id=linea.id)
    db.add(turno)
    db.commit()
    db.refresh(turno)

    operario = Operario(tenant_id=tenant_id, legajo="LEG-R1", nombre_completo="Operario Rendimiento")
    db.add(operario)
    db.commit()
    db.refresh(operario)

    return planta, linea, estacion, turno, operario


def _asignar_y_emitir(db, tenant_id, estacion, turno, operario, estado="OPTIMO", unidades=1, rechazadas=0):
    hoy = datetime.utcnow().date()
    existente = db.exec(
        select(AsignacionTurno).where(
            AsignacionTurno.tenant_id == tenant_id,
            AsignacionTurno.fecha == hoy,
            AsignacionTurno.estacion_fk == estacion.id,
            AsignacionTurno.turno_fk == turno.id,
        )
    ).first()
    if not existente:
        db.add(AsignacionTurno(
            tenant_id=tenant_id, fecha=hoy, estacion_fk=estacion.id,
            operario_fk=operario.id, turno_fk=turno.id,
        ))
        db.commit()

    # BE-P0-06: la atribución real la resuelve SesionOperario, no
    # AsignacionTurno (ver docstring del módulo) -- misma sesión abierta
    # reusada entre llamadas sucesivas de este helper para el mismo
    # (estación, operario), igual que un operario real que sigue logueado.
    sesion_abierta = db.exec(
        select(SesionOperario).where(
            SesionOperario.tenant_id == tenant_id,
            SesionOperario.estacion_fk == estacion.id,
            SesionOperario.operario_fk == operario.id,
            SesionOperario.salida.is_(None),
        )
    ).first()
    if not sesion_abierta:
        db.add(SesionOperario(
            tenant_id=tenant_id, estacion_fk=estacion.id,
            operario_fk=operario.id, turno_fk=turno.id,
            entrada=datetime.utcnow() - timedelta(hours=1),
        ))
        db.commit()

    db.add(LiteEventoProduccion(
        tenant_id=tenant_id, id_estacion=str(estacion.id),
        unidades_procesadas=unidades, unidades_rechazadas=rechazadas,
        estado=estado, delta_t_segundos=45.0,
    ))
    db.commit()


def test_rendimiento_operarios_agrega_por_operario(client, db, tenant_a, gerente_a):
    planta, linea, estacion, turno, operario = _preparar_escenario(db, tenant_a)
    _asignar_y_emitir(db, tenant_a, estacion, turno, operario, estado="OPTIMO", unidades=5)
    _asignar_y_emitir(db, tenant_a, estacion, turno, operario, estado="LENTO", unidades=3)

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/rendimiento-operarios/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    fila = filas[0]
    assert fila["legajo"] == "LEG-R1"
    assert fila["unidades_producidas"] == 8
    assert fila["estaciones_operadas"] == ["Estación Rend"]
    assert fila["distribucion_desempeno"]["optimo"] == 50.0
    assert fila["distribucion_desempeno"]["lento"] == 50.0


def test_rendimiento_operarios_evento_sin_asignacion_no_se_le_atribuye_a_nadie(client, db, tenant_a, gerente_a):
    planta, linea, estacion, turno, operario = _preparar_escenario(db, tenant_a)
    # Evento sin AsignacionTurno previa -- no hay forma de resolver el operario.
    db.add(LiteEventoProduccion(
        tenant_id=tenant_a, id_estacion=str(estacion.id),
        unidades_procesadas=1, estado="OPTIMO",
    ))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/rendimiento-operarios/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert r.json() == []


def test_rendimiento_operarios_filtra_por_operario_id(client, db, tenant_a, gerente_a):
    planta, linea, estacion, turno, operario = _preparar_escenario(db, tenant_a)
    otro = Operario(tenant_id=tenant_a, legajo="LEG-R2", nombre_completo="Otro Operario")
    db.add(otro)
    db.commit()
    db.refresh(otro)

    _asignar_y_emitir(db, tenant_a, estacion, turno, operario, unidades=2)

    estacion2 = Estacion(tenant_id=tenant_a, nombre="Estación Rend 2", tipo="sensor", linea_id=linea.id)
    db.add(estacion2)
    db.commit()
    db.refresh(estacion2)
    _asignar_y_emitir(db, tenant_a, estacion2, turno, otro, unidades=3)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/rendimiento-operarios/",
        params={"operario_id": str(operario.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["operario_id"] == str(operario.id)


def test_rendimiento_operarios_fecha_hasta_menor_a_desde_devuelve_400(client, db, tenant_a, gerente_a):
    planta, *_ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/rendimiento-operarios/",
        params={"fecha_desde": "2026-01-10", "fecha_hasta": "2026-01-01"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_rendimiento_operarios_no_mezcla_tenants(client, db, tenant_a, tenant_b, gerente_a):
    planta_a, linea_a, est_a, turno_a, op_a = _preparar_escenario(db, tenant_a)
    planta_b, linea_b, est_b, turno_b, op_b = _preparar_escenario(db, tenant_b)
    _asignar_y_emitir(db, tenant_a, est_a, turno_a, op_a, unidades=1)
    _asignar_y_emitir(db, tenant_b, est_b, turno_b, op_b, unidades=99)

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/rendimiento-operarios/", headers={"X-Sub-Tenant-Id": str(planta_a.id)})
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["unidades_producidas"] == 1

"""Fase O (auditoría de producción del front, ítem #1): estado en vivo de
una línea para la portada de TYMEO. Antes esa pantalla era 100% mock
(useFactoryStatus.ts nunca llamaba al backend)."""
from datetime import date, datetime, time, timedelta

from app.models.domain import (
    AsignacionSupervisor, AsignacionTurno, Estacion, EstadoOrden, Linea,
    MotivoParada, Operario, OrdenProduccion, ParadaDetectada, EstadoParada,
    Planta, Supervisor, Turno,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta O")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea O")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    est1 = Estacion(tenant_id=tenant_id, nombre="Estación 1", tipo="sensor", linea_id=linea.id, posicion_linea=1)
    est2 = Estacion(tenant_id=tenant_id, nombre="Estación 2", tipo="sensor", linea_id=linea.id, posicion_linea=2)
    db.add(est1)
    db.add(est2)
    db.commit()
    db.refresh(est1)
    db.refresh(est2)

    return planta, linea, est1, est2


def test_linea_en_vivo_sin_datos_devuelve_estructura_vacia_por_estacion(client, db, tenant_a, gerente_a):
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)

    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["linea_id"] == str(linea.id)
    assert body["turno_actual"] is None
    assert body["orden_activa"] is None
    assert len(body["estaciones"]) == 2
    assert all(e["estado"] == "sin_datos" for e in body["estaciones"])


def test_linea_en_vivo_resuelve_turno_por_horario_actual(client, db, tenant_a, gerente_a):
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    ahora = datetime.now().time()
    inicio = time((ahora.hour - 1) % 24, 0)
    fin = time((ahora.hour + 1) % 24, 59)
    turno = Turno(tenant_id=tenant_a, nombre="Turno Actual", hora_inicio=inicio, hora_fin=fin, linea_id=linea.id)
    db.add(turno)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["turno_actual"] == "Turno Actual"


def test_linea_en_vivo_respeta_dias_semana_del_turno(client, db, tenant_a, gerente_a):
    """Fase Q: un turno cuyo horario matchea pero cuyo dias_semana NO
    incluye hoy no debe resolver como turno_actual -- antes (Fase O) el
    día de la semana no se chequeaba en absoluto."""
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    hoy_iso = datetime.now().isoweekday()
    otro_dia = "1" if hoy_iso != 1 else "2"  # cualquier día distinto de hoy

    turno_otro_dia = Turno(
        tenant_id=tenant_a, nombre="Turno de otro día",
        hora_inicio=time(0, 0), hora_fin=time(23, 59), linea_id=linea.id,
        dias_semana=otro_dia,
    )
    db.add(turno_otro_dia)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["turno_actual"] is None  # el horario matchea, el día no


def test_linea_en_vivo_resuelve_turno_de_hoy_por_dia_semana(client, db, tenant_a, gerente_a):
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    hoy_iso = str(datetime.now().isoweekday())

    turno_hoy = Turno(
        tenant_id=tenant_a, nombre="Turno de hoy",
        hora_inicio=time(0, 0), hora_fin=time(23, 59), linea_id=linea.id,
        dias_semana=hoy_iso,
    )
    db.add(turno_hoy)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["turno_actual"] == "Turno de hoy"


def test_linea_en_vivo_estacion_inactiva_se_muestra_grisada_no_oculta(client, db, tenant_a, gerente_a):
    """Feedback de producto (Fase Q): antes una estación inactiva
    desaparecía por completo del listado (filtro Estacion.activa==True),
    rompiendo la continuidad visual del flujo. Ahora se devuelve igual,
    con activa=False, para que el front la muestre grisada en su lugar."""
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    est1.activa = False
    db.add(est1)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["estaciones"]) == 2  # las dos siguen presentes
    fila_est1 = next(e for e in body["estaciones"] if e["estacion_fk"] == str(est1.id))
    fila_est2 = next(e for e in body["estaciones"] if e["estacion_fk"] == str(est2.id))
    assert fila_est1["activa"] is False
    assert fila_est2["activa"] is True


def test_linea_en_vivo_muestra_orden_en_progreso(client, db, tenant_a, gerente_a):
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    orden = OrdenProduccion(
        tenant_id=tenant_a, id_orden=f"OP-VIVO-{linea.id.hex[:6]}", linea_id=linea.id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=None,
    )
    db.add(orden)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["orden_activa"] == orden.id_orden


def test_linea_en_vivo_estacion_con_parada_pendiente_queda_en_parada(client, db, tenant_a, gerente_a):
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    parada = ParadaDetectada(
        tenant_id=tenant_a, estacion_fk=est1.id, inicio=datetime.utcnow(),
        estado=EstadoParada.PENDIENTE,
    )
    db.add(parada)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    fila_est1 = next(e for e in r.json()["estaciones"] if e["estacion_fk"] == str(est1.id))
    assert fila_est1["estado"] == "parada"


def test_linea_en_vivo_resuelve_operario_asignado(client, db, tenant_a, gerente_a):
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    ahora = datetime.now().time()
    turno = Turno(
        tenant_id=tenant_a, nombre="Turno con dotación",
        hora_inicio=time(0, 0), hora_fin=time(23, 59), linea_id=linea.id,
    )
    db.add(turno)
    db.commit()
    db.refresh(turno)

    operario = Operario(tenant_id=tenant_a, legajo="LEG-O1", nombre_completo="Operario Vivo")
    db.add(operario)
    db.commit()
    db.refresh(operario)

    asignacion = AsignacionTurno(
        tenant_id=tenant_a, fecha=date.today(), estacion_fk=est1.id,
        operario_fk=operario.id, turno_fk=turno.id,
    )
    db.add(asignacion)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    fila_est1 = next(e for e in r.json()["estaciones"] if e["estacion_fk"] == str(est1.id))
    assert fila_est1["operario_nombre"] == "Operario Vivo"


def test_linea_en_vivo_resuelve_supervisor_asignado(client, db, tenant_a, gerente_a):
    planta, linea, est1, est2 = _preparar_escenario(db, tenant_a)
    turno = Turno(
        tenant_id=tenant_a, nombre="Turno con supervisor",
        hora_inicio=time(0, 0), hora_fin=time(23, 59), linea_id=linea.id,
    )
    db.add(turno)
    db.commit()
    db.refresh(turno)

    supervisor = Supervisor(tenant_id=tenant_a, legajo="SUP-VIVO", nombre_completo="Supervisor Vivo")
    db.add(supervisor)
    db.commit()
    db.refresh(supervisor)

    db.add(AsignacionSupervisor(
        tenant_id=tenant_a, linea_id=linea.id, turno_id=turno.id, supervisor_id=supervisor.id,
        dias_semana="1,2,3,4,5,6,7", vigencia_desde=date.today() - timedelta(days=1), vigencia_hasta=None,
    ))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["supervisor_actual"] == "Supervisor Vivo"


def test_linea_en_vivo_linea_de_otro_tenant_devuelve_404(client, db, tenant_a, tenant_b, gerente_a):
    planta_a, linea_a, *_ = _preparar_escenario(db, tenant_a)
    planta_b, linea_b, *_ = _preparar_escenario(db, tenant_b)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/linea-en-vivo/",
        params={"linea_id": str(linea_b.id)},
        headers={"X-Sub-Tenant-Id": str(planta_a.id)},
    )
    assert r.status_code == 404

"""Fase DZ (PRD "Day Detail Report" v1.0): endpoint /analytics/dia-radiografia/
-- radiografía de línea/turno para un día puntual (OEE por turno,
supervisor, estaciones, máquinas, operarios, paradas).

Cubre también el cambio del que depende: _calcular_metricas_oee ahora
filtra de verdad por ventana horaria de turno (antes `turno_id` llegaba
hasta la función sin ningún efecto -- ver comentario histórico que
quedó actualizado en analytics.py).

Los eventos se insertan DIRECTO en la base (LiteEventoProduccion), no vía
POST /api/lite/scans -- ese endpoint rechaza timestamps de más de 7 días
o de más de 5 minutos en el futuro (Fase BX, robustez de red), así que
no admite una FECHA fija y determinística como la que necesita este
test. Insertar directo evita ambos guards y permite fijar fecha/hora a
mano, sin ninguna dependencia de datetime.now() (mismo criterio que el
resto de esta sesión: timestamps siempre inyectados, nunca deducidos del
reloj real)."""
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import text

from app.models.domain import (
    AsignacionSupervisor, AsignacionTurno, Estacion, EstadoParada,
    Linea, LiteEventoProduccion, Maquina, MaquinaEstacion, MotivoParada,
    Operario, Planta, SesionOperario, Supervisor, TipoParada, Turno,
    ParadaDetectada as ParadaDetectadaModel,
)
from tests.conftest import autenticar_como

DIA_ISO_TODOS = "1,2,3,4,5,6,7"
AR_OFFSET = timedelta(hours=3)  # America/Buenos_Aires = UTC-3 fijo (sin DST)
FECHA = date(2026, 6, 15)  # fecha fija arbitraria -- irrelevante cuál, dias_semana cubre los 7 días


def _utc_para_hora_local(hora_local: str) -> datetime:
    """'08:30' local Buenos Aires, en FECHA -> datetime UTC naive (mismo
    formato en que se persisten los timestamps reales)."""
    h, m = (int(x) for x in hora_local.split(":"))
    local = datetime(FECHA.year, FECHA.month, FECHA.day, h, m)
    return local + AR_OFFSET


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta DZ")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea DZ")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(
        tenant_id=tenant_id, nombre="Cortadora", tipo="sensor", linea_id=linea.id,
        activa=True, posicion_linea=1,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(
        tenant_id=tenant_id, nombre="Turno 1", linea_id=linea.id,
        hora_inicio="08:00:00", hora_fin="16:00:00", dias_semana=DIA_ISO_TODOS,
    )
    db.add(turno)
    db.commit()
    db.refresh(turno)

    maquina = Maquina(tenant_id=tenant_id, codigo_externo="MOT-01", nombre="Motor Cortadora")
    db.add(maquina)
    db.commit()
    db.refresh(maquina)
    db.add(MaquinaEstacion(tenant_id=tenant_id, maquina_id=maquina.id, estacion_id=estacion.id))

    supervisor = Supervisor(tenant_id=tenant_id, legajo="SUP-1", nombre_completo="Carlos López")
    db.add(supervisor)
    db.commit()
    db.refresh(supervisor)
    db.add(AsignacionSupervisor(
        tenant_id=tenant_id, linea_id=linea.id, turno_id=turno.id, supervisor_id=supervisor.id,
        dias_semana=DIA_ISO_TODOS, vigencia_desde=date(2020, 1, 1),
    ))

    operario = Operario(tenant_id=tenant_id, legajo="OP-1", nombre_completo="Juan Pérez")
    db.add(operario)
    db.commit()
    db.refresh(operario)
    db.add(AsignacionTurno(
        tenant_id=tenant_id, fecha=FECHA, estacion_fk=estacion.id,
        operario_fk=operario.id, turno_fk=turno.id,
    ))
    # BE-P0-06: la atribución por PARADA (p["operario_nombre"]) resuelve
    # contra SesionOperario, no AsignacionTurno (ver docstring en
    # analytics.py) -- sesión cubriendo toda la ventana nominal del turno,
    # mismo horario 08:00-16:00 local. AsignacionTurno arriba sigue
    # existiendo para la dotación planificada (t["operarios"]), que no
    # cambió.
    db.add(SesionOperario(
        tenant_id=tenant_id, estacion_fk=estacion.id,
        operario_fk=operario.id, turno_fk=turno.id,
        entrada=_utc_para_hora_local("08:00"), salida=_utc_para_hora_local("16:00"),
    ))
    db.commit()

    return planta, linea, estacion, turno, maquina, supervisor, operario


def _insertar_evento(db, tenant_id, estacion_id, ts_utc, linea: Linea):
    db.add(LiteEventoProduccion(
        tenant_id=tenant_id, id_estacion=str(estacion_id), timestamp=ts_utc,
        unidades_procesadas=1, unidades_rechazadas=0,
        tiempo_ideal_seg=linea.tiempo_ideal_seg, delta_t_segundos=linea.tiempo_ideal_seg,
        estado="OPTIMO", incluido_oee=True, event_id=uuid.uuid4(),
    ))
    db.commit()


def test_radiografia_completa_de_un_turno(db, client, tenant_a, gerente_a):
    planta, linea, estacion, turno, maquina, supervisor, operario = _preparar_escenario(db, tenant_a)

    # 2 eventos DENTRO de la ventana del turno (08:00-16:00 local).
    _insertar_evento(db, tenant_a, estacion.id, _utc_para_hora_local("08:30"), linea)
    _insertar_evento(db, tenant_a, estacion.id, _utc_para_hora_local("09:00"), linea)

    # Parada no planificada dentro de la ventana (18 min).
    db.add(ParadaDetectadaModel(
        tenant_id=tenant_a, estacion_fk=estacion.id,
        inicio=_utc_para_hora_local("10:00"), fin=_utc_para_hora_local("10:18"),
        duracion_segundos=18 * 60, estado=EstadoParada.CLASIFICADA,
    ))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/dia-radiografia/",
        params={"fecha": FECHA.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["linea_nombre"] == "Línea DZ"
    assert len(body["turnos"]) == 1
    t = body["turnos"][0]
    assert t["nombre"] == "Turno 1"
    assert t["hora_inicio"] == "08:00"
    assert t["hora_fin"] == "16:00"
    assert t["produccion_unidades"] == 2
    assert t["calidad_pct"] == 100.0  # POR_RECHAZO, sin rechazos

    # Supervisor resuelto vía AsignacionSupervisor (regla recurrente).
    assert t["supervisor_id"] == str(supervisor.id)
    assert t["supervisor_nombre"] == "Carlos López"

    # Operario: sin [SALIR] real -> entrada/salida son los bordes
    # nominales del turno, marcado explícitamente como estimado.
    assert len(t["operarios"]) == 1
    op = t["operarios"][0]
    assert op["operario_id"] == str(operario.id)
    assert op["entrada"] == "08:00"
    assert op["salida"] == "16:00"
    assert op["salida_estimada"] is True
    assert op["horas_totales"] == 8.0

    # Máquina vinculada a la estación, uptime derivado de su actividad.
    assert len(t["maquinas"]) == 1
    maq = t["maquinas"][0]
    assert maq["maquina_id"] == str(maquina.id)
    assert maq["operativa"] is True
    assert maq["alertas_cantidad"] == 0

    # Estación: activa, tuvo actividad, cuenta la parada.
    assert len(t["estaciones"]) == 1
    est = t["estaciones"][0]
    assert est["activa_config"] is True
    assert est["tuvo_actividad"] is True
    assert est["paradas_cantidad"] == 1

    # Parada: no planificada, con impacto negativo real en disponibilidad
    # (no None -- eso es justo lo que separa "planificada" de "no
    # planificada" en el impacto).
    assert len(t["paradas"]) == 1
    p = t["paradas"][0]
    assert p["tipo"] == "no_planificada"
    assert p["hora_inicio"] == "10:00"
    assert p["hora_fin"] == "10:18"
    assert p["duracion_minutos"] == 18.0
    assert p["impacto_disponibilidad_pct"] is not None
    assert p["impacto_disponibilidad_pct"] < 0
    assert p["exclusion_oee"] is False
    # Operario resuelto best-effort vía la dotación de esa (estación, turno, fecha).
    assert p["operario_nombre"] == "Juan Pérez"

    # OEE del turno == OEE del día (todo lo que pasó ese día cayó en
    # este único turno).
    assert body["oee_dia_pct"] == t["oee_pct"]
    assert body["produccion_dia_unidades"] == 2


def test_parada_planificada_no_tiene_impacto_en_disponibilidad(db, client, tenant_a, gerente_a):
    planta, linea, estacion, turno, maquina, supervisor, operario = _preparar_escenario(db, tenant_a)
    _insertar_evento(db, tenant_a, estacion.id, _utc_para_hora_local("08:30"), linea)

    motivo = MotivoParada(tenant_id=tenant_a, nombre="Cambio de formato", tipo_parada=TipoParada.PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    db.add(ParadaDetectadaModel(
        tenant_id=tenant_a, estacion_fk=estacion.id, motivo_fk=motivo.id,
        inicio=_utc_para_hora_local("10:00"), fin=_utc_para_hora_local("10:20"),
        duracion_segundos=20 * 60, estado=EstadoParada.CLASIFICADA,
    ))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/dia-radiografia/",
        params={"fecha": FECHA.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    p = r.json()["turnos"][0]["paradas"][0]
    assert p["tipo"] == "planificada"
    assert p["impacto_disponibilidad_pct"] is None


def test_evento_fuera_de_la_ventana_del_turno_no_cuenta_para_ese_turno(db, client, tenant_a, gerente_a):
    """El cambio central de esta fase: _calcular_metricas_oee ahora SÍ
    filtra por ventana horaria de turno -- antes turno_id no tenía
    ningún efecto en el resultado."""
    planta, linea, estacion, turno, maquina, supervisor, operario = _preparar_escenario(db, tenant_a)

    # Evento a las 20:00 local -- 4 horas después de que cerró el único
    # turno del día (16:00). No debería aparecer en el turno.
    _insertar_evento(db, tenant_a, estacion.id, _utc_para_hora_local("20:00"), linea)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/dia-radiografia/",
        params={"fecha": FECHA.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    t = r.json()["turnos"][0]
    assert t["produccion_unidades"] == 0
    assert t["oee_pct"] == 0.0


def test_linea_de_otro_tenant_devuelve_404(db, client, tenant_a, tenant_b, gerente_a, gerente_b):
    """Aislamiento multi-tenant: gerente_b no puede pedir la radiografía
    de una línea de tenant_a, ni siquiera sabiendo su UUID."""
    planta_a, linea_a, *_ = _preparar_escenario(db, tenant_a)

    planta_b = Planta(tenant_id=tenant_b, nombre="Planta B")
    db.add(planta_b)
    db.commit()
    db.refresh(planta_b)

    autenticar_como(gerente_b.id)
    r = client.get(
        "/analytics/dia-radiografia/",
        params={"fecha": FECHA.isoformat(), "linea_id": str(linea_a.id)},
        headers={"X-Sub-Tenant-Id": str(planta_b.id)},
    )
    assert r.status_code == 404


def test_parada_con_exclusion_oee_legado_en_minuscula_no_rompe_la_radiografia(db, client, tenant_a, gerente_a):
    """Fase EZ (bug real, reproducido contra datos de Green Mills):
    ParadaDetectada.exclusion_oee sin `values_callable` hacía que
    SQLAlchemy mapeara por el NAME del enum ("NINGUNA") al leer, pero la
    columna es VARCHAR plano con server_default='ninguna' (el VALUE, en
    minúscula -- ver migración f4a2d891c6e7). Cualquier fila que haya
    recibido ese default directo de Postgres (backfill de ALTER TABLE,
    nunca pasó por el bind processor de la ORM) quedaba con 'ninguna' en
    minúscula y explotaba con LookupError al leerse -- exactamente lo que
    le pasó a /analytics/dia-radiografia/ en producción. Se simula ese
    escenario con un UPDATE crudo (bypass total de la ORM, igual que un
    backfill de migración), no con el ORM -- así el test replica la causa
    real, no sólo el síntoma."""
    planta, linea, estacion, turno, maquina, supervisor, operario = _preparar_escenario(db, tenant_a)
    _insertar_evento(db, tenant_a, estacion.id, _utc_para_hora_local("08:30"), linea)

    parada = ParadaDetectadaModel(
        tenant_id=tenant_a, estacion_fk=estacion.id,
        inicio=_utc_para_hora_local("10:00"), fin=_utc_para_hora_local("10:18"),
        duracion_segundos=18 * 60, estado=EstadoParada.CLASIFICADA,
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)

    db.execute(text("UPDATE paradas_detectadas SET exclusion_oee = 'ninguna' WHERE id = :id"), {"id": str(parada.id)})
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/dia-radiografia/",
        params={"fecha": FECHA.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200, r.text
    p = r.json()["turnos"][0]["paradas"][0]
    assert p["exclusion_oee"] is False

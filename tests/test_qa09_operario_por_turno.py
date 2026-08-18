"""QA-09 (auditoría QA): /analytics/rendimiento-operarios/ agrupaba la
resolución operario<-evento por (estacion_fk, fecha), perdiendo
turno_fk -- con DOS turnos en la misma estación el mismo día (turno
día + turno noche, la dotación real más común), la segunda
AsignacionTurno cargada pisaba a la primera en el dict de lookup, y
TODOS los eventos de ese día terminaban atribuidos a un único operario.

Depende del fix de Fase AM (_resolver_turno_por_horario) para resolver
correctamente el turno de cada evento por horario.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.domain import (
    AsignacionTurno, Estacion, Linea, Operario, Planta, SesionOperario, Turno,
)
from tests.conftest import autenticar_como

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AN", timezone="America/Argentina/Buenos_Aires")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea AN", tiempo_ideal_seg=5, tiempo_lento_seg=500, tiempo_alerta_seg=1000)
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación AN", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _postear(client, credencial, estacion_id, ts_utc):
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id), "timestamp": ts_utc.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201


def test_rendimiento_operarios_distingue_turno_dia_de_turno_noche(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    # Ancla 2 días atrás en hora LOCAL de planta -- margen de sobra
    # contra el límite de 7 días de _validar_rango_timestamp y contra
    # bordes de medianoche real al armar los horarios exactos de abajo.
    dia_local = (datetime.now(TZ) - timedelta(days=2)).date()
    ts_dia_utc = datetime.combine(dia_local, time(10, 0), tzinfo=TZ).astimezone(timezone.utc)
    ts_noche_utc = datetime.combine(dia_local, time(23, 0), tzinfo=TZ).astimezone(timezone.utc)

    turno_dia = Turno(tenant_id=tenant_a, nombre="Día", hora_inicio=time(6, 0), hora_fin=time(14, 0), linea_id=linea.id)
    turno_noche = Turno(tenant_id=tenant_a, nombre="Noche", hora_inicio=time(22, 0), hora_fin=time(6, 0), linea_id=linea.id)
    op_dia = Operario(tenant_id=tenant_a, legajo=f"OD-{uuid.uuid4().hex[:6]}", nombre_completo="Operario Día", activo=True)
    op_noche = Operario(tenant_id=tenant_a, legajo=f"ON-{uuid.uuid4().hex[:6]}", nombre_completo="Operario Noche", activo=True)
    db.add_all([turno_dia, turno_noche, op_dia, op_noche])
    db.commit()
    db.refresh(turno_dia)
    db.refresh(turno_noche)
    db.refresh(op_dia)
    db.refresh(op_noche)

    db.add_all([
        AsignacionTurno(tenant_id=tenant_a, fecha=dia_local, estacion_fk=estacion.id, operario_fk=op_dia.id, turno_fk=turno_dia.id),
        AsignacionTurno(tenant_id=tenant_a, fecha=dia_local, estacion_fk=estacion.id, operario_fk=op_noche.id, turno_fk=turno_noche.id),
    ])
    db.commit()

    # BE-P0-06: la atribución real la resuelve SesionOperario (arriba
    # sigue existiendo AsignacionTurno, dotación/staffing planificado sin
    # cambios). Dos sesiones reales, una por turno, cada una cubriendo su
    # propia ventana horaria -- exactamente el escenario "dos turnos,
    # mismo día, misma estación" que este test existe para probar.
    def _local_utc_naive(hora: time, dia=dia_local):
        return datetime.combine(dia, hora, tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)

    db.add_all([
        SesionOperario(
            tenant_id=tenant_a, estacion_fk=estacion.id, operario_fk=op_dia.id, turno_fk=turno_dia.id,
            entrada=_local_utc_naive(time(6, 0)), salida=_local_utc_naive(time(14, 0)),
        ),
        SesionOperario(
            tenant_id=tenant_a, estacion_fk=estacion.id, operario_fk=op_noche.id, turno_fk=turno_noche.id,
            entrada=_local_utc_naive(time(22, 0)),
            salida=_local_utc_naive(time(6, 0), dia=dia_local + timedelta(days=1)),
        ),
    ])
    db.commit()

    _postear(client, credencial, estacion.id, ts_dia_utc)
    _postear(client, credencial, estacion.id, ts_noche_utc)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/rendimiento-operarios/",
        params={"fecha_desde": (dia_local - timedelta(days=1)).isoformat(), "fecha_hasta": (dia_local + timedelta(days=1)).isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    por_operario = {f["operario_id"]: f for f in r.json()}

    assert str(op_dia.id) in por_operario
    assert str(op_noche.id) in por_operario
    # El bug real: sin el fix, uno de los dos termina con 2 unidades (las
    # de los dos turnos juntas) y el otro con 0 -- ninguno de los dos
    # aparece con exactamente 1.
    assert por_operario[str(op_dia.id)]["unidades_producidas"] == 1
    assert por_operario[str(op_noche.id)]["unidades_producidas"] == 1

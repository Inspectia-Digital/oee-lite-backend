"""QA-06 (auditoría QA): validar_planta() sólo confirma que el usuario
tiene acceso a la planta activa (X-Sub-Tenant-Id) -- NO que la entidad
puntual que está escribiendo (parada, estación, asignación) pertenezca
realmente a esa planta. La lectura vía listados sí filtraba por planta
(JOIN + WHERE Linea.planta_id == context.sub_tenant_id); el agujero
era puntual en las escrituras por UUID directo (`db.get`/`select` sólo
contra tenant_id). Motiva especialmente la incorporación del rol
Encargado (acceso angosto, pensado para no poder tocar nada fuera de
su planta) -- pero el gap afectaba a cualquier rol, no sólo a ese.

Escenario general de cada test: dos plantas del mismo tenant, una
entidad real en la Planta B, y un intento de escribirla mientras el
contexto activo (X-Sub-Tenant-Id) es la Planta A -- debe dar 404, no
200/204.
"""
import uuid
from datetime import datetime, time, timedelta, timezone

from app.models.domain import (
    AsignacionSupervisor, AsignacionTurno, Estacion, Linea, MotivoParada,
    Operario, ParadaDetectada, Planta, Supervisor, TipoParada, Turno,
)
from tests.conftest import autenticar_como


def _dos_plantas(db, tenant_id):
    planta_a = Planta(tenant_id=tenant_id, nombre="Planta A (activa)")
    planta_b = Planta(tenant_id=tenant_id, nombre="Planta B (ajena)")
    db.add(planta_a)
    db.add(planta_b)
    db.commit()
    db.refresh(planta_a)
    db.refresh(planta_b)
    linea_b = Linea(tenant_id=tenant_id, planta_id=planta_b.id, nombre="Línea B")
    db.add(linea_b)
    db.commit()
    db.refresh(linea_b)
    estacion_b = Estacion(tenant_id=tenant_id, nombre="Estación B", tipo="sensor", linea_id=linea_b.id, activa=True)
    db.add(estacion_b)
    db.commit()
    db.refresh(estacion_b)
    return planta_a, planta_b, linea_b, estacion_b


def _motivo(db, tenant_id, tipo=TipoParada.NO_PLANIFICADA):
    m = MotivoParada(tenant_id=tenant_id, nombre=f"Motivo {uuid.uuid4().hex[:6]}", tipo_parada=tipo)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def test_clasificar_parada_de_otra_planta_devuelve_404(client, db, tenant_a, gerente_a):
    planta_a, planta_b, linea_b, estacion_b = _dos_plantas(db, tenant_a)
    motivo = _motivo(db, tenant_a)
    parada = ParadaDetectada(
        tenant_id=tenant_a, estacion_fk=estacion_b.id,
        inicio=datetime.now(timezone.utc) - timedelta(hours=1), estado="pendiente",
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)

    autenticar_como(gerente_a.id)
    r = client.patch(
        f"/supervisor/paradas/{parada.id}/clasificar",
        json={"motivo_fk": str(motivo.id)},
        headers={"X-Sub-Tenant-Id": str(planta_a.id)},
    )
    assert r.status_code == 404


def test_registrar_parada_planificada_en_estacion_de_otra_planta_devuelve_404(client, db, tenant_a, gerente_a):
    planta_a, planta_b, linea_b, estacion_b = _dos_plantas(db, tenant_a)
    motivo = _motivo(db, tenant_a, tipo=TipoParada.PLANIFICADA)

    autenticar_como(gerente_a.id)
    inicio = datetime.now(timezone.utc) + timedelta(hours=1)
    r = client.post(
        "/supervisor/paradas/planificadas",
        json={
            "estacion_fk": str(estacion_b.id),
            "motivo_fk": str(motivo.id),
            "inicio": inicio.isoformat(),
            "fin": (inicio + timedelta(hours=1)).isoformat(),
        },
        headers={"X-Sub-Tenant-Id": str(planta_a.id)},
    )
    assert r.status_code == 404


def test_eliminar_parada_planificada_de_otra_planta_devuelve_404(client, db, tenant_a, gerente_a):
    planta_a, planta_b, linea_b, estacion_b = _dos_plantas(db, tenant_a)
    parada = ParadaDetectada(
        tenant_id=tenant_a, estacion_fk=estacion_b.id,
        inicio=datetime.now(timezone.utc) + timedelta(hours=1),
        fin=datetime.now(timezone.utc) + timedelta(hours=2),
        estado="clasificada", origen="PLANIFICADA",
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)

    autenticar_como(gerente_a.id)
    r = client.delete(
        f"/supervisor/paradas/{parada.id}",
        headers={"X-Sub-Tenant-Id": str(planta_a.id)},
    )
    assert r.status_code == 404


def test_liberar_estacion_de_asignacion_de_otra_planta_devuelve_404(client, db, tenant_a, gerente_a):
    planta_a, planta_b, linea_b, estacion_b = _dos_plantas(db, tenant_a)
    operario = Operario(tenant_id=tenant_a, legajo=f"OP-{uuid.uuid4().hex[:6]}", nombre_completo="Operario B", activo=True)
    turno = Turno(tenant_id=tenant_a, linea_id=linea_b.id, nombre="Turno B", hora_inicio=time(6, 0), hora_fin=time(14, 0))
    db.add(operario)
    db.add(turno)
    db.commit()
    db.refresh(operario)
    db.refresh(turno)
    asignacion = AsignacionTurno(
        tenant_id=tenant_a, fecha=datetime.now(timezone.utc).date(),
        estacion_fk=estacion_b.id, operario_fk=operario.id, turno_fk=turno.id,
    )
    db.add(asignacion)
    db.commit()
    db.refresh(asignacion)

    autenticar_como(gerente_a.id)
    r = client.delete(
        f"/supervisor/asignaciones/{asignacion.id}",
        headers={"X-Sub-Tenant-Id": str(planta_a.id)},
    )
    assert r.status_code == 404


def test_eliminar_asignacion_supervisor_de_otra_planta_devuelve_404(client, db, tenant_a, gerente_a):
    planta_a, planta_b, linea_b, estacion_b = _dos_plantas(db, tenant_a)
    turno = Turno(tenant_id=tenant_a, linea_id=linea_b.id, nombre="Turno B", hora_inicio=time(6, 0), hora_fin=time(14, 0))
    supervisor = Supervisor(tenant_id=tenant_a, legajo=f"SUP-{uuid.uuid4().hex[:6]}", nombre_completo="Supervisor B", activo=True)
    db.add(turno)
    db.add(supervisor)
    db.commit()
    db.refresh(turno)
    db.refresh(supervisor)
    regla = AsignacionSupervisor(
        tenant_id=tenant_a, linea_id=linea_b.id, turno_id=turno.id, supervisor_id=supervisor.id,
        dias_semana="1,2,3,4,5", vigencia_desde=datetime.now(timezone.utc).date(),
    )
    db.add(regla)
    db.commit()
    db.refresh(regla)

    autenticar_como(gerente_a.id)
    r = client.delete(
        f"/asignaciones/supervisor/{regla.id}",
        headers={"X-Sub-Tenant-Id": str(planta_a.id)},
    )
    assert r.status_code == 404


def test_clasificar_parada_de_la_propia_planta_sigue_funcionando(client, db, tenant_a, gerente_a):
    """Regresión: el fix no debe romper el caso normal (parada de la
    misma planta activa)."""
    planta_a, planta_b, linea_b, estacion_b = _dos_plantas(db, tenant_a)
    motivo = _motivo(db, tenant_a)
    parada = ParadaDetectada(
        tenant_id=tenant_a, estacion_fk=estacion_b.id,
        inicio=datetime.now(timezone.utc) - timedelta(hours=1), estado="pendiente",
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)

    autenticar_como(gerente_a.id)
    r = client.patch(
        f"/supervisor/paradas/{parada.id}/clasificar",
        json={"motivo_fk": str(motivo.id)},
        headers={"X-Sub-Tenant-Id": str(planta_b.id)},  # la planta correcta esta vez
    )
    assert r.status_code == 200
    assert r.json()["estado"] == "clasificada"

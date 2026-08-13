"""Rol Encargado (pedido explícito del usuario): intermedio entre
Supervisor y Operario -- tiene cuenta web, pero el acceso es
deliberadamente angosto: sólo ver/clasificar paradas. Confirmado con el
usuario: paradas PLANIFICADAS (programar a futuro) queda excluido, es
"resto de las funciones de supervisor", no "clasificar paradas".

Hallazgo que motivó este archivo: antes de agregar el rol, ninguno de los
endpoints de /supervisor (dotación, asignación de supervisores, paradas
planificadas, eventos/live) chequeaba rol -- sólo pertenencia a la planta
(validar_planta). Agregar el rol nuevo sin restringir esos endpoints le
habría dado acceso a TODO lo de Supervisor, no sólo a paradas. Este
archivo prueba tanto lo que Encargado puede hacer como lo que no, y que
los roles existentes (Supervisor/Gerencia/Producción/SuperAdmin) siguen
teniendo acceso -- son restricciones nuevas sobre endpoints que antes
estaban abiertos a cualquier rol con planta asignada.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from app.models.domain import (
    Estacion, EstadoParada, Linea, MotivoParada, Operario, ParadaDetectada,
    Planta, RolUsuario, Supervisor, TipoParada, Turno, UsuarioPlanta,
)
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Encargado")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Encargado")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación Encargado", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    turno = Turno(tenant_id=tenant_id, nombre="Mañana", hora_inicio="06:00", hora_fin="14:00", linea_id=linea.id)
    db.add(turno)
    db.commit()
    db.refresh(turno)
    operario = Operario(tenant_id=tenant_id, legajo="OP-ENC-1", nombre_completo="Operario Encargado")
    db.add(operario)
    db.commit()
    db.refresh(operario)
    supervisor = Supervisor(tenant_id=tenant_id, legajo="SUP-ENC-1", nombre_completo="Supervisor Encargado")
    db.add(supervisor)
    db.commit()
    db.refresh(supervisor)
    return planta, linea, estacion, turno, operario, supervisor


def _usuario_con_planta(db, tenant_id, rol, planta_id):
    usuario = crear_usuario(db, tenant_id, rol)
    db.add(UsuarioPlanta(tenant_id=tenant_id, usuario_id=usuario.id, planta_id=planta_id, activo=True))
    db.commit()
    return usuario


def _crear_parada_pendiente(db, tenant_id, estacion_id):
    motivo = MotivoParada(tenant_id=tenant_id, nombre="Falla mecánica", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    parada = ParadaDetectada(
        tenant_id=tenant_id, estacion_fk=estacion_id, motivo_fk=None,
        inicio=datetime.now(timezone.utc).replace(tzinfo=None), estado=EstadoParada.PENDIENTE,
        origen="AUTOMATICA",
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)
    return parada, motivo


# ---------- Lo que Encargado SÍ puede hacer ----------

def test_encargado_puede_ver_paradas_pendientes(client, db, tenant_a):
    planta, linea, estacion, *_ = _preparar_escenario(db, tenant_a)
    _crear_parada_pendiente(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.get("/supervisor/paradas-pendientes", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_encargado_puede_clasificar_parada(client, db, tenant_a):
    planta, linea, estacion, *_ = _preparar_escenario(db, tenant_a)
    parada, motivo = _crear_parada_pendiente(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.patch(
        f"/supervisor/paradas/{parada.id}/clasificar",
        json={"motivo_fk": str(motivo.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["estado"] == "clasificada"


def test_encargado_puede_ver_historial_paradas(client, db, tenant_a):
    planta, linea, estacion, *_ = _preparar_escenario(db, tenant_a)
    _crear_parada_pendiente(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.get("/supervisor/paradas", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert len(r.json()) == 1


# ---------- Lo que Encargado NO puede hacer ----------

def test_encargado_no_puede_programar_parada_planificada(client, db, tenant_a):
    planta, linea, estacion, *_ = _preparar_escenario(db, tenant_a)
    motivo = MotivoParada(tenant_id=tenant_a, nombre="Cambio de formato", tipo_parada=TipoParada.PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    inicio = datetime.now(timezone.utc) + timedelta(hours=1)
    r = client.post(
        "/supervisor/paradas/planificadas",
        json={
            "estacion_fk": str(estacion.id), "motivo_fk": str(motivo.id),
            "inicio": inicio.isoformat(), "fin": (inicio + timedelta(minutes=30)).isoformat(),
        },
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 403


def test_encargado_no_puede_eliminar_parada_planificada(client, db, tenant_a):
    planta, linea, estacion, *_ = _preparar_escenario(db, tenant_a)
    parada = ParadaDetectada(
        tenant_id=tenant_a, estacion_fk=estacion.id,
        inicio=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        estado=EstadoParada.CLASIFICADA, origen="PLANIFICADA",
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.delete(f"/supervisor/paradas/{parada.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 403


def test_encargado_no_puede_ver_dotacion(client, db, tenant_a):
    planta, linea, *_ = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    hoy = date.today().isoformat()
    r = client.get(f"/supervisor/asignaciones/?fecha={hoy}&linea_id={linea.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 403


def test_encargado_no_puede_asignar_dotacion(client, db, tenant_a):
    planta, linea, estacion, turno, operario, _ = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.post(
        "/supervisor/asignaciones/",
        json={
            "fecha": date.today().isoformat(), "estacion_fk": str(estacion.id),
            "operario_fk": str(operario.id), "turno_fk": str(turno.id),
        },
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 403


def test_encargado_no_puede_ver_eventos_live(client, db, tenant_a):
    planta, *_ = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.get("/supervisor/eventos/live", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 403


def test_encargado_no_puede_ver_asignacion_supervisores(client, db, tenant_a):
    planta, *_ = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.get("/asignaciones/supervisor/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 403


def test_encargado_no_puede_asignar_supervisor(client, db, tenant_a):
    planta, linea, estacion, turno, operario, supervisor = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.post(
        "/asignaciones/supervisor/",
        json={
            "linea_id": str(linea.id), "turno_id": str(turno.id), "supervisor_id": str(supervisor.id),
            "dias_semana": [1, 2, 3, 4, 5], "vigencia_desde": date.today().isoformat(),
        },
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 403


def test_encargado_no_puede_avanzar_plan(client, db, tenant_a):
    planta, linea, *_ = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan encargado"},
        headers=headers,
    )
    # crear_plan también requiere Gerencia -- confirma con un 403 aparte,
    # pero lo que importa acá es avanzar_orden, así que se crea el plan
    # como Gerencia y sólo se prueba avanzar_orden como Encargado.
    if plan.status_code == 403:
        gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
        db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=gerente.id, planta_id=planta.id, activo=True))
        db.commit()
        autenticar_como(gerente.id)
        plan = client.post(
            "/config/planes/",
            json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan encargado"},
            headers=headers,
        )
        autenticar_como(encargado.id)
    assert plan.status_code == 201
    plan_id = plan.json()["id"]

    r = client.post(f"/supervisor/planes/{plan_id}/avanzar-orden/", headers=headers)
    assert r.status_code == 403


# ---------- Regresión: los roles existentes siguen teniendo acceso ----------

def test_supervisor_sigue_pudiendo_asignar_y_ver_dotacion(client, db, tenant_a):
    planta, linea, estacion, turno, operario, _ = _preparar_escenario(db, tenant_a)
    supervisor_usuario = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    autenticar_como(supervisor_usuario.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    r = client.post(
        "/supervisor/asignaciones/",
        json={
            "fecha": date.today().isoformat(), "estacion_fk": str(estacion.id),
            "operario_fk": str(operario.id), "turno_fk": str(turno.id),
        },
        headers=headers,
    )
    assert r.status_code == 201

    r = client.get(f"/supervisor/asignaciones/?fecha={date.today().isoformat()}&linea_id={linea.id}", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_gerencia_sigue_pudiendo_ver_eventos_live_y_asignar_supervisor(client, db, tenant_a):
    planta, linea, estacion, turno, operario, supervisor = _preparar_escenario(db, tenant_a)
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=gerente.id, planta_id=planta.id, activo=True))
    db.commit()
    autenticar_como(gerente.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    r = client.get("/supervisor/eventos/live", headers=headers)
    assert r.status_code == 200

    r = client.post(
        "/asignaciones/supervisor/",
        json={
            "linea_id": str(linea.id), "turno_id": str(turno.id), "supervisor_id": str(supervisor.id),
            "dias_semana": [1, 2, 3, 4, 5], "vigencia_desde": date.today().isoformat(),
        },
        headers=headers,
    )
    assert r.status_code == 201


def test_produccion_sigue_pudiendo_programar_parada_planificada(client, db, tenant_a):
    planta, linea, estacion, *_ = _preparar_escenario(db, tenant_a)
    motivo = MotivoParada(tenant_id=tenant_a, nombre="Mantenimiento programado", tipo_parada=TipoParada.PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    produccion_usuario = _usuario_con_planta(db, tenant_a, RolUsuario.PRODUCCION, planta.id)
    autenticar_como(produccion_usuario.id)

    inicio = datetime.now(timezone.utc) + timedelta(hours=1)
    r = client.post(
        "/supervisor/paradas/planificadas",
        json={
            "estacion_fk": str(estacion.id), "motivo_fk": str(motivo.id),
            "inicio": inicio.isoformat(), "fin": (inicio + timedelta(minutes=30)).isoformat(),
        },
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200

"""Fase DC (pedido del usuario, coherencia de RBAC): dos gaps encontrados
al revisar "quién gestiona la asignación de supervisores".

1. Gestión del REGISTRO de un Supervisor (crear/editar/desactivar/
   vincular a usuario web, personas.py): antes sólo Gerencia/SuperAdmin
   -- se agrega Producción. Sigue excluyendo al rol Supervisor (no debe
   poder gestionar el alta de otros supervisores) y a Operarios (sin
   cambios, no cubierto acá).

2. Gestión de la REGLA de asignación día/turno/línea (AsignacionSupervisor,
   operacion.py): antes usaba ROLES_SUPERVISION_COMPLETA (incluye al
   propio rol Supervisor) por descuido -- se angosta a
   ROLES_GESTION_ASIGNACION_SUPERVISOR (Gerencia/Producción/SuperAdmin).
"""
import uuid
from datetime import date, time

from app.models.domain import Linea, Planta, RolUsuario, Supervisor, Turno, UsuarioPlanta
from tests.conftest import autenticar_como, crear_usuario


def _usuario_con_planta(db, tenant_id, rol, planta_id):
    usuario = crear_usuario(db, tenant_id, rol)
    db.add(UsuarioPlanta(tenant_id=tenant_id, usuario_id=usuario.id, planta_id=planta_id, activo=True))
    db.commit()
    return usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta RBAC Supervisores")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(
        tenant_id=tenant_id, planta_id=planta.id, nombre="Línea RBAC Supervisores",
        tiempo_ideal_seg=100, tiempo_lento_seg=150, tiempo_alerta_seg=200,
    )
    db.add(linea)
    db.commit()
    db.refresh(linea)
    turno = Turno(tenant_id=tenant_id, nombre="Mañana", hora_inicio=time(6, 0), hora_fin=time(14, 0), linea_id=linea.id)
    db.add(turno)
    db.commit()
    db.refresh(turno)
    supervisor = Supervisor(tenant_id=tenant_id, legajo=f"SUP-{uuid.uuid4().hex[:6]}", nombre_completo="Supervisor de Prueba")
    db.add(supervisor)
    db.commit()
    db.refresh(supervisor)
    return planta, linea, turno, supervisor


# ---------- 1) CRUD del registro de Supervisor (personas.py) ----------

def test_produccion_puede_crear_supervisor(client, db, tenant_a):
    planta, _, _, _ = _preparar_escenario(db, tenant_a)
    produccion = _usuario_con_planta(db, tenant_a, RolUsuario.PRODUCCION, planta.id)
    autenticar_como(produccion.id)

    r = client.post(
        "/config/supervisores/",
        json={"legajo": f"SUP-{uuid.uuid4().hex[:6]}", "nombre_completo": "Nuevo Supervisor"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201


def test_supervisor_no_puede_crear_supervisor(client, db, tenant_a):
    planta, _, _, _ = _preparar_escenario(db, tenant_a)
    supervisor_actor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    autenticar_como(supervisor_actor.id)

    r = client.post(
        "/config/supervisores/",
        json={"legajo": f"SUP-{uuid.uuid4().hex[:6]}", "nombre_completo": "No Debería Poder"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 403


def test_produccion_puede_vincular_supervisor_a_usuario_web(client, db, tenant_a):
    """`LinkSupervisorUserDialog` (frontend) usa este mismo PATCH con sólo
    `usuario_id` -- Producción también puede vincular/desvincular."""
    planta, _, _, supervisor = _preparar_escenario(db, tenant_a)
    produccion = _usuario_con_planta(db, tenant_a, RolUsuario.PRODUCCION, planta.id)
    autenticar_como(produccion.id)

    r = client.patch(
        f"/config/supervisores/{supervisor.id}",
        json={"nombre_completo": "Supervisor Editado por Producción"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["nombre_completo"] == "Supervisor Editado por Producción"


def test_supervisor_no_puede_editar_supervisor(client, db, tenant_a):
    planta, _, _, supervisor = _preparar_escenario(db, tenant_a)
    supervisor_actor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    autenticar_como(supervisor_actor.id)

    r = client.patch(
        f"/config/supervisores/{supervisor.id}",
        json={"nombre_completo": "No Debería Poder"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 403


def test_gerencia_sigue_pudiendo_desactivar_supervisor(client, db, tenant_a):
    """Regresión: Gerencia/SuperAdmin no pierden el acceso que ya tenían."""
    planta, _, _, supervisor = _preparar_escenario(db, tenant_a)
    gerente = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    autenticar_como(gerente.id)

    r = client.delete(f"/config/supervisores/{supervisor.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200


# ---------- 2) Regla de asignación día/turno/línea (operacion.py) ----------

def _payload_asignacion(linea, turno, supervisor):
    return {
        "linea_id": str(linea.id),
        "turno_id": str(turno.id),
        "supervisor_id": str(supervisor.id),
        "dias_semana": [1, 2, 3, 4, 5],
        "vigencia_desde": date.today().isoformat(),
    }


def test_produccion_puede_crear_asignacion_supervisor(client, db, tenant_a):
    planta, linea, turno, supervisor = _preparar_escenario(db, tenant_a)
    produccion = _usuario_con_planta(db, tenant_a, RolUsuario.PRODUCCION, planta.id)
    autenticar_como(produccion.id)

    r = client.post(
        "/asignaciones/supervisor/",
        json=_payload_asignacion(linea, turno, supervisor),
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201


def test_supervisor_no_puede_crear_asignacion_supervisor(client, db, tenant_a):
    """Antes de Fase DC, ROLES_SUPERVISION_COMPLETA incluía al propio rol
    Supervisor acá -- podía decidir quién supervisa qué línea, algo que
    debería ser una decisión de gestión, no operativa."""
    planta, linea, turno, supervisor = _preparar_escenario(db, tenant_a)
    supervisor_actor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    autenticar_como(supervisor_actor.id)

    r = client.post(
        "/asignaciones/supervisor/",
        json=_payload_asignacion(linea, turno, supervisor),
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 403


def test_supervisor_no_puede_listar_asignaciones_supervisor(client, db, tenant_a):
    planta, _, _, _ = _preparar_escenario(db, tenant_a)
    supervisor_actor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    autenticar_como(supervisor_actor.id)

    r = client.get("/asignaciones/supervisor/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 403


def test_supervisor_no_puede_eliminar_asignacion_supervisor(client, db, tenant_a):
    planta, linea, turno, supervisor = _preparar_escenario(db, tenant_a)
    gerente = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    autenticar_como(gerente.id)
    creada = client.post(
        "/asignaciones/supervisor/",
        json=_payload_asignacion(linea, turno, supervisor),
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    ).json()

    supervisor_actor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    autenticar_como(supervisor_actor.id)
    r = client.delete(f"/asignaciones/supervisor/{creada['id']}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 403


def test_gerencia_sigue_pudiendo_gestionar_asignacion_supervisor(client, db, tenant_a):
    """Regresión: Gerencia/SuperAdmin no pierden el acceso que ya tenían."""
    planta, linea, turno, supervisor = _preparar_escenario(db, tenant_a)
    gerente = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    autenticar_como(gerente.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    r_crear = client.post("/asignaciones/supervisor/", json=_payload_asignacion(linea, turno, supervisor), headers=headers)
    assert r_crear.status_code == 201

    r_listar = client.get("/asignaciones/supervisor/", headers=headers)
    assert r_listar.status_code == 200
    assert len(r_listar.json()) == 1

    r_eliminar = client.delete(f"/asignaciones/supervisor/{r_crear.json()['id']}", headers=headers)
    assert r_eliminar.status_code == 204

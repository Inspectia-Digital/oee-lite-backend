"""Fase D.2 (suspensión de tenant) y D.3 (RBAC geolocalizado)."""
import uuid

from sqlmodel import select

from app.models.domain import Estacion, Linea, Planta, RolUsuario, Tenant, UsuarioPlanta
from tests.conftest import autenticar_como, crear_usuario


def _crear_estacion_con_planta(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta RBAC")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea RBAC")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación RBAC", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


# ---------- Suspensión de tenant (D.2) ----------

def test_ui_suspendida_bloquea_endpoint_humano_pero_no_me(client, db, tenant_a, gerente_a):
    t = db.get(Tenant, tenant_a)
    t.estado = "ui_suspendida"
    db.add(t)
    db.commit()

    autenticar_como(gerente_a.id)
    assert client.get("/config/lineas/").status_code == 403
    assert client.get("/accesos/usuarios/me").status_code == 200


def test_suspension_total_bloquea_humano_y_me_sigue_vivo(client, db, tenant_a, gerente_a):
    t = db.get(Tenant, tenant_a)
    t.estado = "suspension_total"
    db.add(t)
    db.commit()

    autenticar_como(gerente_a.id)
    assert client.get("/config/lineas/").status_code == 403
    assert client.get("/accesos/usuarios/me").status_code == 200


def test_solo_superadmin_puede_cambiar_estado_tenant(client, db, tenant_a, gerente_a, superadmin):
    autenticar_como(gerente_a.id)
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}/estado", json={"estado": "ui_suspendida"})
    assert r.status_code == 403

    autenticar_como(superadmin.id)
    r = client.patch(f"/accesos/superadmin/tenants/{tenant_a}/estado", json={"estado": "activo"})
    assert r.status_code == 200


# ---------- RBAC geolocalizado (D.3) ----------

def test_supervisor_sin_asignacion_recibe_403_en_endpoint_operativo(client, db, tenant_a):
    _, _, estacion = _crear_estacion_con_planta(db, tenant_a)
    planta = db.exec(select(Planta).where(Planta.tenant_id == tenant_a)).first()
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)

    autenticar_como(supervisor.id)
    r = client.get("/supervisor/paradas-pendientes", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 403


def test_supervisor_con_asignacion_accede_al_endpoint_operativo(client, db, tenant_a):
    _, _, estacion = _crear_estacion_con_planta(db, tenant_a)
    planta = db.exec(select(Planta).where(Planta.tenant_id == tenant_a)).first()
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)

    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=supervisor.id, planta_id=planta.id))
    db.commit()

    autenticar_como(supervisor.id)
    r = client.get("/supervisor/paradas-pendientes", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200


def test_gerencia_no_necesita_asignacion_a_planta(client, db, tenant_a, gerente_a):
    _, _, _ = _crear_estacion_con_planta(db, tenant_a)
    planta = db.exec(select(Planta).where(Planta.tenant_id == tenant_a)).first()

    autenticar_como(gerente_a.id)
    r = client.get("/supervisor/paradas-pendientes", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200


def test_asignar_planta_rechaza_roles_no_permitidos(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta X")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    r = client.post(
        "/accesos/mi-empresa/usuario-planta",
        json={"usuario_id": str(gerente_a.id), "planta_id": str(planta.id)},
    )
    assert r.status_code == 400


def test_endpoint_asignacion_retroactiva_esta_deshabilitado(client):
    r = client.post(
        "/supervisor/operarios/asignar-retroactivo",
        json={
            "estacion_fk": str(uuid.uuid4()), "operario_fk": str(uuid.uuid4()),
            "inicio": "2026-01-01T00:00:00", "fin": "2026-01-01T01:00:00",
        },
    )
    assert r.status_code == 410

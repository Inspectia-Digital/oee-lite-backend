"""Fase F (InspectIA OS): matriz de permisos por módulo y planta."""
from sqlmodel import select

from app.models.domain import ModuloPermiso, Planta, RolUsuario, Tenant
from tests.conftest import autenticar_como, crear_usuario


def _crear_planta(db, tenant_id, nombre="Planta F"):
    planta = Planta(tenant_id=tenant_id, nombre=nombre)
    db.add(planta)
    db.commit()
    db.refresh(planta)
    return planta


def test_me_incluye_permisos_de_alcance_completo_para_gerencia(client, db, tenant_a, gerente_a):
    t = db.get(Tenant, tenant_a)
    t.modulos_contratados = "tymeo,oee-hub"
    db.add(t)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200
    permisos = r.json()["permisos"]
    modulos = {p["modulo"] for p in permisos}
    assert modulos == {"tymeo", "oee-hub"}
    assert all(p["planta_id"] is None for p in permisos)
    assert all(p["rol"] == "gerencia" for p in permisos)


def test_me_supervisor_sin_permiso_no_ve_modulos(client, db, tenant_a):
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    autenticar_como(supervisor.id)
    r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200
    assert r.json()["permisos"] == []


def test_me_supervisor_ve_solo_su_modulo_y_planta_asignados(client, db, tenant_a):
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    planta = _crear_planta(db, tenant_a)

    db.add(ModuloPermiso(
        tenant_id=tenant_a, usuario_id=supervisor.id, modulo="tymeo",
        planta_id=planta.id, rol=RolUsuario.SUPERVISOR,
    ))
    db.commit()

    autenticar_como(supervisor.id)
    r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200
    permisos = r.json()["permisos"]
    assert len(permisos) == 1
    assert permisos[0]["modulo"] == "tymeo"
    assert permisos[0]["planta_id"] == str(planta.id)


def test_asignar_permiso_modulo_rechaza_roles_de_alcance_completo(client, db, tenant_a, gerente_a):
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/accesos/mi-empresa/modulo-permiso",
        json={"usuario_id": str(gerente_a.id), "modulo": "tymeo", "planta_id": str(planta.id), "rol": "gerencia"},
    )
    assert r.status_code == 400


def test_asignar_permiso_modulo_duplicado_devuelve_409(client, db, tenant_a, gerente_a):
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)

    payload = {"usuario_id": str(supervisor.id), "modulo": "tymeo", "planta_id": str(planta.id), "rol": "supervisor"}
    r1 = client.post("/accesos/mi-empresa/modulo-permiso", json=payload)
    assert r1.status_code == 201

    r2 = client.post("/accesos/mi-empresa/modulo-permiso", json=payload)
    assert r2.status_code == 409


def test_quitar_permiso_modulo_lo_desactiva_no_lo_borra(client, db, tenant_a, gerente_a):
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    planta = _crear_planta(db, tenant_a)
    autenticar_como(gerente_a.id)

    payload = {"usuario_id": str(supervisor.id), "modulo": "tymeo", "planta_id": str(planta.id), "rol": "supervisor"}
    creado = client.post("/accesos/mi-empresa/modulo-permiso", json=payload).json()

    r = client.delete(f"/accesos/mi-empresa/modulo-permiso/{creado['id']}")
    assert r.status_code == 200

    fila = db.get(ModuloPermiso, creado["id"])
    assert fila is not None
    assert fila.activo is False


def test_permiso_de_otro_tenant_no_es_visible(client, db, tenant_a, tenant_b, gerente_a, gerente_b):
    supervisor_b = crear_usuario(db, tenant_b, RolUsuario.SUPERVISOR)
    planta_b = _crear_planta(db, tenant_b, "Planta B")

    autenticar_como(gerente_b.id)
    creado = client.post(
        "/accesos/mi-empresa/modulo-permiso",
        json={"usuario_id": str(supervisor_b.id), "modulo": "tymeo", "planta_id": str(planta_b.id), "rol": "supervisor"},
    ).json()

    autenticar_como(gerente_a.id)
    r = client.get("/accesos/mi-empresa/modulo-permiso")
    assert r.status_code == 200
    assert all(p["id"] != creado["id"] for p in r.json())

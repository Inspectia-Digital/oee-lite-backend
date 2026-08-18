"""P1-A (PRD Go-Live Green Mills, sección 5): Producción puede crear/
editar usuarios y plantas, pero nunca puede escalar hacia Gerencia o
SuperAdmin -- ni crear, ni modificar, ni revocar accesos de alguien con
esos roles.

Hallazgo real de pasada al implementar esto: POST /accesos/mi-empresa/
usuarios ya bloqueaba a Producción por completo (ni siquiera podía crear
un Encargado), y PATCH /accesos/mi-empresa/usuarios/{id} no tenía NINGÚN
piso de rol -- cualquier usuario autenticado del tenant podía cambiarle
el rol o revocarle el acceso a cualquier otro. Los dos se corrigen acá."""
import uuid

from app.models.domain import RolUsuario
from tests.conftest import autenticar_como, crear_usuario


def _email_unico(prefijo: str) -> str:
    # Mismo criterio que el resto de la suite (legajo=f"LEG-{uuid...}") --
    # esta suite corre contra Postgres real persistente, no una base
    # descartable por test; email tiene UNIQUE real en la tabla.
    return f"{prefijo}.{uuid.uuid4().hex[:8]}@test.com"


def test_produccion_crea_usuario_encargado(client, db, tenant_a):
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    autenticar_como(produccion.id)
    r = client.post(
        "/accesos/mi-empresa/usuarios",
        json={"email": _email_unico("encargado"), "nombre": "Enc", "apellido": "Argado", "rol": "encargado"},
    )
    assert r.status_code == 201, r.text


def test_produccion_no_puede_crear_usuario_gerencia(client, db, tenant_a):
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    autenticar_como(produccion.id)
    r = client.post(
        "/accesos/mi-empresa/usuarios",
        json={"email": _email_unico("gerente"), "nombre": "Ger", "apellido": "Ente", "rol": "gerencia"},
    )
    assert r.status_code == 403


def test_produccion_no_puede_crear_usuario_superadmin(client, db, tenant_a):
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    autenticar_como(produccion.id)
    r = client.post(
        "/accesos/mi-empresa/usuarios",
        json={"email": _email_unico("super"), "nombre": "Sup", "apellido": "Er", "rol": "superadmin"},
    )
    assert r.status_code == 403


def test_produccion_no_puede_modificar_un_gerente_existente(client, db, tenant_a):
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(produccion.id)
    r = client.patch(f"/accesos/mi-empresa/usuarios/{gerente.auth0_id}", json={"nombre": "Otro Nombre"})
    assert r.status_code == 403


def test_produccion_no_puede_revocar_acceso_de_un_gerente(client, db, tenant_a):
    """"Revocar acceso" = activo=False vía el mismo endpoint de update."""
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(produccion.id)
    r = client.patch(f"/accesos/mi-empresa/usuarios/{gerente.auth0_id}", json={"activo": False})
    assert r.status_code == 403


def test_produccion_no_puede_ascender_un_encargado_a_gerencia(client, db, tenant_a):
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    encargado = crear_usuario(db, tenant_a, RolUsuario.ENCARGADO)
    autenticar_como(produccion.id)
    r = client.patch(f"/accesos/mi-empresa/usuarios/{encargado.auth0_id}", json={"rol": "gerencia"})
    assert r.status_code == 403


def test_produccion_puede_modificar_un_encargado(client, db, tenant_a):
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    encargado = crear_usuario(db, tenant_a, RolUsuario.ENCARGADO)
    autenticar_como(produccion.id)
    r = client.patch(f"/accesos/mi-empresa/usuarios/{encargado.auth0_id}", json={"nombre": "Nuevo Nombre"})
    assert r.status_code == 200


def test_operario_no_puede_llamar_al_update_de_usuarios(client, db, tenant_a):
    """Hallazgo de pasada: antes este endpoint no tenía piso de rol en
    absoluto -- ni siquiera un Operario estaba bloqueado."""
    operario_web = crear_usuario(db, tenant_a, RolUsuario.OPERARIO)
    otro = crear_usuario(db, tenant_a, RolUsuario.ENCARGADO)
    autenticar_como(operario_web.id)
    r = client.patch(f"/accesos/mi-empresa/usuarios/{otro.auth0_id}", json={"activo": False})
    assert r.status_code == 403


def test_produccion_crea_planta(client, db, tenant_a):
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    autenticar_como(produccion.id)
    r = client.post(
        "/accesos/mi-empresa/sub-tenants",
        json={"nombre": "Planta Nueva P1A", "ubicacion": "N/A", "timezone": "America/Buenos_Aires"},
    )
    assert r.status_code == 201, r.text


def test_produccion_edita_timezone_de_planta(client, db, tenant_a):
    produccion = crear_usuario(db, tenant_a, RolUsuario.PRODUCCION)
    autenticar_como(produccion.id)
    r_crear = client.post(
        "/accesos/mi-empresa/sub-tenants",
        json={"nombre": "Planta P1A TZ", "ubicacion": "N/A", "timezone": "America/Buenos_Aires"},
    )
    assert r_crear.status_code == 201
    planta_id = r_crear.json()["id"]

    r = client.patch(
        f"/accesos/mi-empresa/sub-tenants/{planta_id}",
        json={"timezone": "America/Mexico_City"},
    )
    assert r.status_code == 200
    assert r.json()["timezone"] == "America/Mexico_City"

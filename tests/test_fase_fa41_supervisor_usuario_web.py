"""Fase FA.4.1: vínculo Supervisor <-> cuenta web (usuario_id).

BUG REAL que motivó esta fase (encontrado auditando
PRD_SEGMENTACION_PLANES_IMPLEMENTACION.md contra el código): el
frontend ya mandaba `usuario_id` a PATCH /config/supervisores/{id}
(LinkSupervisorUserDialog.tsx -> useAsignacionSupervisores.ts:117) y la
pantalla de Personal ya mostraba la columna "acceso" leyéndolo
(PersonalCrudPanel.tsx), pero ni el modelo `Supervisor` ni el schema
`SupervisorUpdate` tenían el campo. Pydantic descarta los campos extra
en silencio, así que el endpoint respondía 200, el frontend cantaba
"Usuario web vinculado" y NO SE PERSISTÍA NADA -- Personal mostraba
"sin acceso web" para siempre.

test_vincular_usuario_web_persiste es el test de regresión de ese bug
exacto: falla (el vínculo se pierde) sin la columna + el campo en el
schema."""
import uuid

from app.models.domain import RolUsuario, Supervisor
from tests.conftest import autenticar_como, crear_usuario


def _crear_supervisor(client, legajo: str = None) -> dict:
    legajo = legajo or f"SUP-{uuid.uuid4().hex[:8]}"
    r = client.post("/config/supervisores/", json={"legajo": legajo, "nombre_completo": "Supervisor Test"})
    assert r.status_code == 201, r.text
    return r.json()


def test_supervisor_nace_sin_usuario_web(client, db, tenant_a, gerente_a):
    """Estado normal, no un error: dado de alta operativamente (asignable
    a línea/turno) pero todavía sin cuenta web."""
    autenticar_como(gerente_a.id)
    supervisor = _crear_supervisor(client)
    assert supervisor["usuario_id"] is None
    assert supervisor["usuario_email"] is None


def test_vincular_usuario_web_persiste(client, db, tenant_a, gerente_a):
    """REGRESIÓN del bug: antes esto devolvía 200 y no guardaba nada."""
    autenticar_como(gerente_a.id)
    supervisor = _crear_supervisor(client)
    usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)

    r = client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": str(usuario.id)})
    assert r.status_code == 200, r.text
    assert r.json()["usuario_id"] == str(usuario.id)

    # Lo que realmente fallaba: releer y que el vínculo siga ahí.
    r = client.get(f"/config/supervisores/{supervisor['id']}")
    assert r.status_code == 200
    assert r.json()["usuario_id"] == str(usuario.id)

    db.expire_all()
    en_db = db.get(Supervisor, uuid.UUID(supervisor["id"]))
    assert en_db.usuario_id == usuario.id


def test_vincular_devuelve_el_email_del_usuario(client, db, tenant_a, gerente_a):
    """PersonalCrudPanel muestra el email, no el UUID -- vive en otra
    tabla, por eso hace falta SupervisorOut."""
    autenticar_como(gerente_a.id)
    supervisor = _crear_supervisor(client)
    usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    usuario.email = "supervisor.web@test.com"
    db.add(usuario)
    db.commit()

    r = client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": str(usuario.id)})
    assert r.status_code == 200
    assert r.json()["usuario_email"] == "supervisor.web@test.com"


def test_desvincular_usuario_web(client, db, tenant_a, gerente_a):
    """`usuario_id: null` explícito desvincula -- distinto de no mandar
    el campo (que no lo toca). Lo resuelve exclude_unset."""
    autenticar_como(gerente_a.id)
    supervisor = _crear_supervisor(client)
    usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": str(usuario.id)})

    r = client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": None})
    assert r.status_code == 200
    assert r.json()["usuario_id"] is None
    assert r.json()["usuario_email"] is None


def test_editar_otro_campo_no_pisa_el_vinculo(client, db, tenant_a, gerente_a):
    """Renombrar al supervisor no debe desvincularle la cuenta web --
    el caso que rompería si se usara `datos.get("usuario_id")` en vez de
    `"usuario_id" in datos`."""
    autenticar_como(gerente_a.id)
    supervisor = _crear_supervisor(client)
    usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": str(usuario.id)})

    r = client.patch(f"/config/supervisores/{supervisor['id']}", json={"nombre_completo": "Nombre Nuevo"})
    assert r.status_code == 200
    assert r.json()["nombre_completo"] == "Nombre Nuevo"
    assert r.json()["usuario_id"] == str(usuario.id)  # sigue vinculado


def test_no_se_puede_vincular_usuario_de_otro_tenant(client, db, tenant_a, tenant_b, gerente_a):
    autenticar_como(gerente_a.id)
    supervisor = _crear_supervisor(client)
    usuario_ajeno = crear_usuario(db, tenant_b, RolUsuario.SUPERVISOR)

    r = client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": str(usuario_ajeno.id)})
    assert r.status_code == 404


def test_un_usuario_no_puede_estar_en_dos_supervisores(client, db, tenant_a, gerente_a):
    """El frontend calcula `usuariosOcupados` asumiendo esta regla --
    sin el 409 del backend sería sólo una convención de UI."""
    autenticar_como(gerente_a.id)
    sup_a = _crear_supervisor(client)
    sup_b = _crear_supervisor(client)
    usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)

    r = client.patch(f"/config/supervisores/{sup_a['id']}", json={"usuario_id": str(usuario.id)})
    assert r.status_code == 200

    r = client.patch(f"/config/supervisores/{sup_b['id']}", json={"usuario_id": str(usuario.id)})
    assert r.status_code == 409


def test_revincular_el_mismo_usuario_al_mismo_supervisor_no_es_conflicto(client, db, tenant_a, gerente_a):
    """Guardar dos veces sin cambiar nada no debe dar 409 -- la
    validación excluye al propio supervisor."""
    autenticar_como(gerente_a.id)
    supervisor = _crear_supervisor(client)
    usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)

    client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": str(usuario.id)})
    r = client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": str(usuario.id)})
    assert r.status_code == 200


def test_listar_supervisores_incluye_el_vinculo(client, db, tenant_a, gerente_a):
    """PersonalCrudPanel arma la tabla desde el listado, no pidiendo
    cada supervisor de a uno."""
    autenticar_como(gerente_a.id)
    supervisor = _crear_supervisor(client)
    usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    client.patch(f"/config/supervisores/{supervisor['id']}", json={"usuario_id": str(usuario.id)})

    r = client.get("/config/supervisores/")
    assert r.status_code == 200
    fila = next((s for s in r.json() if s["id"] == supervisor["id"]), None)
    assert fila is not None
    assert fila["usuario_id"] == str(usuario.id)

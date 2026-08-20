"""Fase EV (pedido del usuario: "si creo un usuario desde el front se
cree en auth0"): crear_usuario_tenant/crear_usuario_b2b (admin.py) ahora
intentan dar de alta al invitado directamente en Auth0 -- best-effort,
ver invitar_usuario_en_auth0 (onboarding.py). Sin credenciales de
Management API configuradas (caso local/tests por default), cae al
placeholder mock de siempre -- mismo comportamiento que antes de este
fix, ver test_..._sin_credenciales_cae_a_mock más abajo."""
from unittest.mock import patch

from app.models.domain import RolUsuario
from tests.conftest import autenticar_como, crear_usuario


def _email_unico(prefijo: str) -> str:
    import uuid
    return f"{prefijo}-{uuid.uuid4().hex[:8]}@empresa-real.com"


def _auth0_id_unico(prefijo: str) -> str:
    """DB persistente sin rollback por test (conftest.py) -- un literal
    fijo de auth0_id colisiona con la unique constraint en reruns."""
    import uuid
    return f"auth0|{prefijo}-{uuid.uuid4().hex[:8]}"


# ---------- POST /accesos/mi-empresa/usuarios ----------

def test_crear_usuario_tenant_sin_credenciales_cae_a_mock(client, db, tenant_a):
    """Regresión: sin AUTH0_MGMT_CLIENT_ID/SECRET configuradas (default en
    tests), el alta sigue funcionando exactamente igual que antes de Fase
    EV -- placeholder mock, sin romper nada."""
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    email = _email_unico("sin-mgmt-api")

    r = client.post(
        "/accesos/mi-empresa/usuarios",
        json={"email": email, "nombre": "Nueva", "apellido": "Persona", "rol": "supervisor"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["auth0_creado"] is False
    assert data["ticket_url"] is None


def test_crear_usuario_tenant_crea_en_auth0_mockeado(client, db, tenant_a):
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    email = _email_unico("con-mgmt-api")
    auth0_id_real = _auth0_id_unico("real-id")

    with patch("app.core.onboarding.crear_usuario_auth0", return_value=auth0_id_real), \
         patch("app.core.onboarding.crear_ticket_cambio_password", return_value="https://dev-x.auth0.com/tickets/xyz"):
        r = client.post(
            "/accesos/mi-empresa/usuarios",
            json={"email": email, "nombre": "Nueva", "apellido": "Persona", "rol": "supervisor"},
        )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["auth0_creado"] is True
    assert data["ticket_url"] == "https://dev-x.auth0.com/tickets/xyz"

    from app.models.domain import UsuarioSaaS
    from sqlmodel import select
    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.id == data["id"])).first()
    assert usuario_db.auth0_id == auth0_id_real
    assert not usuario_db.auth0_id.startswith("auth0|mock_")


def test_crear_usuario_tenant_auth0_creado_pero_ticket_falla(client, db, tenant_a):
    """El alta en Auth0 funciona, pero generar el link de contraseña falla
    (ej. el M2M no tiene create:user_tickets por algún motivo puntual) --
    igual se guarda el auth0_id REAL, no se pierde el alta por esto."""
    from fastapi import HTTPException
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    email = _email_unico("ticket-falla")

    with patch("app.core.onboarding.crear_usuario_auth0", return_value=_auth0_id_unico("sin-ticket")), \
         patch("app.core.onboarding.crear_ticket_cambio_password", side_effect=HTTPException(503, "no se pudo")):
        r = client.post(
            "/accesos/mi-empresa/usuarios",
            json={"email": email, "nombre": "Nueva", "apellido": "Persona", "rol": "supervisor"},
        )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["auth0_creado"] is True
    assert data["ticket_url"] is None


def test_crear_usuario_tenant_auth0_id_explicito_no_intenta_crear_en_auth0(client, db, tenant_a):
    """UsuarioCreate.auth0_id explícito (poco común) se respeta tal cual --
    no se llama a Auth0 en absoluto."""
    gerente = crear_usuario(db, tenant_a, RolUsuario.GERENCIA)
    autenticar_como(gerente.id)
    email = _email_unico("id-explicito")

    with patch("app.core.onboarding.crear_usuario_auth0") as mock_crear:
        r = client.post(
            "/accesos/mi-empresa/usuarios",
            json={
                "email": email, "nombre": "Nueva", "apellido": "Persona", "rol": "supervisor",
                "auth0_id": _auth0_id_unico("ya-conocido-de-antes").replace("auth0|", "google-oauth2|"),
            },
        )
    assert r.status_code == 201, r.text
    mock_crear.assert_not_called()
    data = r.json()
    assert data["auth0_creado"] is False


# ---------- POST /accesos/superadmin/usuarios ----------

def test_crear_usuario_b2b_crea_en_auth0_mockeado(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    email = _email_unico("b2b-con-mgmt-api")
    auth0_id_real = _auth0_id_unico("real-id-b2b")

    with patch("app.core.onboarding.crear_usuario_auth0", return_value=auth0_id_real), \
         patch("app.core.onboarding.crear_ticket_cambio_password", return_value="https://dev-x.auth0.com/tickets/b2b"):
        r = client.post(
            "/accesos/superadmin/usuarios",
            json={"tenant_id": tenant_a, "email": email, "rol": "gerencia", "nombre": "B2B", "apellido": "Test"},
        )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["auth0_creado"] is True
    assert data["ticket_url"] == "https://dev-x.auth0.com/tickets/b2b"
    assert data["usuario"]["auth0_id"] == auth0_id_real


def test_crear_usuario_b2b_sin_credenciales_cae_a_mock(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    email = _email_unico("b2b-sin-mgmt-api")

    r = client.post(
        "/accesos/superadmin/usuarios",
        json={"tenant_id": tenant_a, "email": email, "rol": "gerencia", "nombre": "B2B", "apellido": "Test"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["auth0_creado"] is False
    assert data["usuario"]["auth0_id"].startswith("auth0|mock_")


# ---------- Wiring real de crear_usuario_auth0 (no sólo el mock a nivel de módulo) ----------

def test_crear_usuario_auth0_wiring_real_mockeado(monkeypatch):
    """Mismo patrón que test_management_api_email_lookup_mockeada -- valida
    que crear_usuario_auth0 arma la llamada real correctamente (URL,
    headers, connection configurada), no sólo que el mock de arriba
    funciona."""
    from app.core.auth0_management import crear_usuario_auth0

    monkeypatch.setattr("app.core.config.settings.AUTH0_MGMT_CLIENT_ID", "test_client_id")
    monkeypatch.setattr("app.core.config.settings.AUTH0_MGMT_CLIENT_SECRET", "test_secret")

    llamadas = {}

    class _RespuestaFalsa:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def _post_falso(url, **kwargs):
        if "/oauth/token" in url:
            return _RespuestaFalsa({"access_token": "fake-mgmt-token"})
        assert "/api/v2/users" in url
        llamadas["json"] = kwargs["json"]
        llamadas["headers"] = kwargs["headers"]
        return _RespuestaFalsa({"user_id": "auth0|nuevo-real-123"})

    with patch("app.core.auth0_management.requests.post", side_effect=_post_falso):
        resultado = crear_usuario_auth0("persona@empresa.com", "Persona", "Apellido")

    assert resultado == "auth0|nuevo-real-123"
    assert llamadas["json"]["email"] == "persona@empresa.com"
    assert llamadas["json"]["connection"] == "Username-Password-Authentication"
    assert llamadas["json"]["email_verified"] is False
    assert "password" in llamadas["json"] and len(llamadas["json"]["password"]) > 20
    assert llamadas["headers"]["Authorization"] == "Bearer fake-mgmt-token"

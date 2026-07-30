"""Fase J: logo de tenant (vía URL) + reset-password (Auth0 Management API).

El reset-password llama a un servicio externo real (Auth0); se mockea sólo
esa llamada HTTP saliente -- todo lo demás (RBAC, lookup en la DB real)
corre sin mocks, igual que el resto de la suite.
"""
from unittest.mock import patch

from app.models.domain import Tenant, RolUsuario
from tests.conftest import autenticar_como, crear_usuario


def test_actualizar_logo_por_url(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/accesos/mi-empresa/tenant/logo", json={"logo_url": "https://cdn.example.com/logo.png"})
    assert r.status_code == 200
    assert r.json()["logo_url"] == "https://cdn.example.com/logo.png"

    tenant_db = db.get(Tenant, tenant_a)
    assert tenant_db.logo_url == "https://cdn.example.com/logo.png"


def test_actualizar_logo_requiere_gerencia_o_superadmin(client, db, tenant_a):
    operario = crear_usuario(db, tenant_a, RolUsuario.OPERARIO)
    autenticar_como(operario.id)
    r = client.post("/accesos/mi-empresa/tenant/logo", json={"logo_url": "https://cdn.example.com/logo.png"})
    assert r.status_code == 403


def test_reset_password_sin_credenciales_devuelve_503(client, db, tenant_a, superadmin, gerente_a):
    autenticar_como(superadmin.id)
    r = client.post(f"/accesos/superadmin/usuarios/{gerente_a.auth0_id}/reset-password")
    assert r.status_code == 503


def test_reset_password_requiere_superadmin(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(f"/accesos/superadmin/usuarios/{gerente_a.auth0_id}/reset-password")
    assert r.status_code == 403


def test_reset_password_usuario_inexistente_404(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/accesos/superadmin/usuarios/auth0|no-existe/reset-password")
    assert r.status_code == 404


def test_reset_password_exitoso_con_management_api_mockeada(client, db, tenant_a, superadmin, gerente_a, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AUTH0_MGMT_CLIENT_ID", "test_client_id")
    monkeypatch.setattr("app.core.config.settings.AUTH0_MGMT_CLIENT_SECRET", "test_secret")

    class _RespuestaFalsa:
        def raise_for_status(self):
            pass

        def json(self):
            if self._es_token:
                return {"access_token": "fake-mgmt-token"}
            return {"ticket": "https://dev-x.auth0.com/tickets/abc123"}

        def __init__(self, es_token):
            self._es_token = es_token

    llamadas = []

    def _post_falso(url, **kwargs):
        llamadas.append(url)
        return _RespuestaFalsa(es_token="/oauth/token" in url)

    with patch("app.core.auth0_management.requests.post", side_effect=_post_falso):
        autenticar_como(superadmin.id)
        r = client.post(f"/accesos/superadmin/usuarios/{gerente_a.auth0_id}/reset-password")

    assert r.status_code == 200
    assert r.json()["ticket_url"] == "https://dev-x.auth0.com/tickets/abc123"
    assert len(llamadas) == 2

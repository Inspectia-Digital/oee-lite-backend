"""Fase K (auditoría QA #17): request-id correlacionable por request."""
import uuid

from tests.conftest import autenticar_como


def test_respuesta_incluye_x_request_id_generado(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200
    assert "x-request-id" in r.headers
    # Es un UUID válido (formato esperado cuando el cliente no manda uno propio).
    uuid.UUID(r.headers["x-request-id"])


def test_respeta_x_request_id_entrante(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    propio = "mi-request-id-de-prueba"
    r = client.get("/accesos/usuarios/me", headers={"X-Request-Id": propio})
    assert r.status_code == 200
    assert r.headers["x-request-id"] == propio

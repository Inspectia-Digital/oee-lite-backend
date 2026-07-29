"""Fase A: ausencia de rutas de emergencia, health checks, config productiva."""
import pytest

from app.core.config import Settings


def test_rutas_de_emergencia_no_existen(client):
    for path in ["/ruta-secreta", "/ascender-estanislao"]:
        assert client.get(path).status_code == 404
    assert client.post("/setup/primer-admin").status_code == 404


def test_health_live_no_depende_de_la_base(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_ready_verifica_la_base_real(client):
    r = client.get("/health/ready")
    assert r.status_code == 200


def test_health_alias_sigue_funcionando(client):
    assert client.get("/health").status_code == 200


def test_produccion_rechaza_cors_vacio():
    with pytest.raises(Exception):
        Settings(
            DATABASE_URL="postgresql+psycopg2://x:x@localhost/x",
            ENVIRONMENT="production",
            CORS_ORIGINS="",
            AUTH0_DOMAIN="x.auth0.com",
            AUTH0_AUDIENCE="https://api.x.com",
        )


def test_produccion_rechaza_cors_wildcard():
    with pytest.raises(Exception):
        Settings(
            DATABASE_URL="postgresql+psycopg2://x:x@localhost/x",
            ENVIRONMENT="production",
            CORS_ORIGINS="*",
            AUTH0_DOMAIN="x.auth0.com",
            AUTH0_AUDIENCE="https://api.x.com",
        )


def test_produccion_rechaza_database_echo():
    with pytest.raises(Exception):
        Settings(
            DATABASE_URL="postgresql+psycopg2://x:x@localhost/x",
            ENVIRONMENT="production",
            CORS_ORIGINS="https://app.real.com",
            DATABASE_ECHO=True,
            AUTH0_DOMAIN="x.auth0.com",
            AUTH0_AUDIENCE="https://api.x.com",
        )


def test_produccion_rechaza_auto_create_tables():
    with pytest.raises(Exception):
        Settings(
            DATABASE_URL="postgresql+psycopg2://x:x@localhost/x",
            ENVIRONMENT="production",
            CORS_ORIGINS="https://app.real.com",
            AUTO_CREATE_TABLES=True,
            AUTH0_DOMAIN="x.auth0.com",
            AUTH0_AUDIENCE="https://api.x.com",
        )


def test_produccion_exige_auth0():
    with pytest.raises(Exception):
        Settings(
            DATABASE_URL="postgresql+psycopg2://x:x@localhost/x",
            ENVIRONMENT="production",
            CORS_ORIGINS="https://app.real.com",
        )


def test_produccion_configuracion_valida_no_falla():
    s = Settings(
        DATABASE_URL="postgresql+psycopg2://x:x@localhost/x",
        ENVIRONMENT="production",
        CORS_ORIGINS="https://app.real.com",
        AUTH0_DOMAIN="x.auth0.com",
        AUTH0_AUDIENCE="https://api.x.com",
    )
    assert s.is_production is True


def test_development_no_exige_nada_de_lo_anterior():
    s = Settings(
        DATABASE_URL="postgresql+psycopg2://x:x@localhost/x",
        ENVIRONMENT="development",
    )
    assert s.is_production is False

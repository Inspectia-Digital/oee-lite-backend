"""Fixtures compartidas. Corre contra Postgres real (docker-compose local),
nunca contra mocks -- así lo pide el HANDOFF para RBAC/triggers/reglas de
negocio. Requiere DATABASE_URL apuntando a una base con las migraciones
de Alembic ya aplicadas (alembic upgrade head) antes de correr pytest.
"""
import os
import sys
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://oeelite_user:oeelite_password@127.0.0.1:5433/oeelite_db")
os.environ.setdefault("ENVIRONMENT", "development")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from main import app
from app.core.auth import get_usuario_actual
from app.core.database import engine
from app.models.domain import UsuarioSaaS, RolUsuario, Tenant, EstadoTenant


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


@pytest.fixture
def db():
    with Session(engine) as session:
        yield session


def _id_unico(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def crear_tenant(db: Session, nombre_prefijo: str = "tenant_test") -> str:
    """Cada test usa un tenant_id único para no interferir con otros tests
    ni con datos reales de springwall/green_mills."""
    tenant_id = _id_unico(nombre_prefijo)
    t = Tenant(id=tenant_id, nombre=f"Test {tenant_id}", estado=EstadoTenant.ACTIVO)
    db.add(t)
    db.commit()
    return tenant_id


def crear_usuario(db: Session, tenant_id: str, rol: RolUsuario) -> UsuarioSaaS:
    u = UsuarioSaaS(auth0_id=_id_unico("test|user"), tenant_id=tenant_id, rol=rol, activo=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def tenant_a(db):
    return crear_tenant(db, "tenant_a")


@pytest.fixture
def tenant_b(db):
    return crear_tenant(db, "tenant_b")


@pytest.fixture
def gerente_a(db, tenant_a):
    return crear_usuario(db, tenant_a, RolUsuario.GERENCIA)


@pytest.fixture
def gerente_b(db, tenant_b):
    return crear_usuario(db, tenant_b, RolUsuario.GERENCIA)


@pytest.fixture
def superadmin(db, tenant_a):
    return crear_usuario(db, tenant_a, RolUsuario.SUPERADMIN)


@pytest.fixture(autouse=True)
def limpiar_overrides():
    """Evita que un override de un test se filtre al siguiente."""
    yield
    app.dependency_overrides.pop(get_usuario_actual, None)


def autenticar_como(usuario_id):
    """Reemplaza get_usuario_actual para simular un login ya resuelto, sin
    necesitar un JWT real de Auth0 (igual que se hizo durante todo el
    desarrollo de estas fases)."""
    def _fake():
        with Session(engine) as s:
            u = s.get(UsuarioSaaS, usuario_id)
            s.expunge(u)
            return u
    app.dependency_overrides[get_usuario_actual] = _fake

"""Fase K: fail-closed en resolución de planta/tenant (auditoría QA #3 y #4).

Antes: una planta inválida o de otra empresa degradaba silenciosamente a
"vista global" (X-Sub-Tenant-Id ignorado); impersonar un tenant inexistente
no se validaba; y las verificaciones de suspensión dejaban pasar cuando el
tenant era None. Ahora todo eso corta con un error explícito.
"""
import pytest
import uuid

from app.core.auth import verificar_no_suspension_total, _verificar_acceso_humano_habilitado
from app.models.domain import Planta, RolUsuario
from tests.conftest import autenticar_como, crear_usuario


def test_planta_inexistente_devuelve_404_no_vista_global(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/config/lineas/", headers={"X-Sub-Tenant-Id": str(uuid.uuid4())})
    assert r.status_code == 404


def test_planta_de_otro_tenant_devuelve_404(client, db, tenant_a, tenant_b, gerente_a):
    planta_b = Planta(tenant_id=tenant_b, nombre="Planta de Otro Tenant")
    db.add(planta_b)
    db.commit()
    db.refresh(planta_b)

    autenticar_como(gerente_a.id)
    r = client.get("/config/lineas/", headers={"X-Sub-Tenant-Id": str(planta_b.id)})
    assert r.status_code == 404


def test_planta_valida_sigue_funcionando(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta Real")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    r = client.get("/config/lineas/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200


def test_impersonar_tenant_inexistente_devuelve_404(client, db, tenant_a, superadmin):
    autenticar_como(superadmin.id)
    r = client.get("/accesos/mi-empresa/tenant?tenant_id=tenant-que-no-existe")
    assert r.status_code == 404


def test_impersonar_tenant_existente_sigue_funcionando(client, db, tenant_a, tenant_b, superadmin):
    autenticar_como(superadmin.id)
    r = client.get(f"/accesos/mi-empresa/tenant?tenant_id={tenant_b}")
    assert r.status_code == 200


def test_verificaciones_suspension_cortan_si_tenant_es_none():
    with pytest.raises(Exception) as exc_info:
        verificar_no_suspension_total(None)
    assert exc_info.value.status_code == 403

    with pytest.raises(Exception) as exc_info:
        _verificar_acceso_humano_habilitado(None)
    assert exc_info.value.status_code == 403

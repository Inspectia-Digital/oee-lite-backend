"""Fase K/M: resolución de planta/tenant (auditoría QA #3 y #4, y su
reversión parcial en Fase M).

La Fase K hizo fail-closed un X-Sub-Tenant-Id inválido (404 duro) a nivel
del contexto base -- pero eso afecta a *todos* los endpoints que dependen
de obtener_contexto_tenant_humano, incluidos los que ni siquiera usan
sub_tenant_id (admin.py, plantas.py: gestionan el tenant/las plantas en
sí, no operan "dentro" de una ya seleccionada). En la práctica bloqueaba
crear la primera planta, editar la empresa, invitar usuarios, etc. cada
vez que el front mandaba un X-Sub-Tenant-Id viejo o inválido -- exactamente
lo esperable antes de tener ninguna planta real cargada.

Fase M revierte esa parte puntual: una planta inválida o de otra empresa
ahora vuelve a limpiarse a sub_tenant_id=None en vez de cortar con 404.
Esto NO reabre el hueco de seguridad original: los endpoints que sí
necesitan una planta válida (operacion.py, analytics.py) ya exigen por su
cuenta que sub_tenant_id esté presente (400 "seleccione planta") desde
antes de la Fase K -- eso sigue vigente. Lo que sí se mantiene igual: la
validación de impersonar un tenant inexistente y las verificaciones de
suspensión con tenant=None.
"""
import pytest
import uuid

from app.core.auth import verificar_no_suspension_total, _verificar_acceso_humano_habilitado
from app.models.domain import Planta, RolUsuario
from tests.conftest import autenticar_como, crear_usuario


def test_planta_inexistente_se_limpia_a_vista_sin_planta(client, db, tenant_a, gerente_a):
    """/config/lineas/ no usa sub_tenant_id -- una planta inválida no debe
    bloquear el listado, sólo queda sin filtrar por planta."""
    autenticar_como(gerente_a.id)
    r = client.get("/config/lineas/", headers={"X-Sub-Tenant-Id": str(uuid.uuid4())})
    assert r.status_code == 200


def test_planta_de_otro_tenant_se_limpia_no_escala_acceso(client, db, tenant_a, tenant_b, gerente_a):
    """Un ID de planta de OTRO tenant tampoco matchea (la query ya filtra
    por tenant_activo) -- se limpia igual que una planta inexistente, sin
    filtrar por ninguna planta, nunca ve datos de tenant_b."""
    planta_b = Planta(tenant_id=tenant_b, nombre="Planta de Otro Tenant")
    db.add(planta_b)
    db.commit()
    db.refresh(planta_b)

    autenticar_como(gerente_a.id)
    r = client.get("/config/lineas/", headers={"X-Sub-Tenant-Id": str(planta_b.id)})
    assert r.status_code == 200


def test_crear_planta_no_se_bloquea_por_sub_tenant_invalido(client, db, tenant_a, gerente_a):
    """El caso concreto que motivó la Fase M: crear la primera planta no
    debe depender de que ya exista una planta válida seleccionada."""
    autenticar_como(gerente_a.id)
    r = client.post(
        "/accesos/mi-empresa/sub-tenants",
        json={"nombre": "Primera Planta"},
        headers={"X-Sub-Tenant-Id": "algo-invalido-o-viejo"},
    )
    assert r.status_code == 201


def test_endpoint_operativo_sigue_exigiendo_planta_valida(client, db, tenant_a):
    """Lo que SÍ debe seguir protegido: un endpoint que realmente usa
    sub_tenant_id (operacion.py) sigue exigiendo una planta -- un ID
    inválido se limpia a None, y eso ya dispara su propio 400 (vigente
    desde antes de la Fase K, no depende del fail-closed revertido)."""
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    autenticar_como(supervisor.id)
    r = client.get("/supervisor/paradas-pendientes", headers={"X-Sub-Tenant-Id": str(uuid.uuid4())})
    assert r.status_code == 400


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

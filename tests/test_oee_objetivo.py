"""Fase N (auditoría de producción del front, ítem #10): objetivo de OEE
configurable por tenant. Antes estaba hardcodeado en el front (75 en la
tendencia, 85 en el Command Center); ahora es un campo real de Tenant,
expuesto por el mismo endpoint self-service que ya existía para branding
y tolerancias."""
from tests.conftest import autenticar_como


def test_tenant_trae_objetivo_default(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/accesos/mi-empresa/tenant")
    assert r.status_code == 200
    assert r.json()["oee_objetivo_pct"] == 85.0


def test_gerencia_actualiza_objetivo_oee(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.patch("/accesos/mi-empresa/tenant", json={"oee_objetivo_pct": 78.5})
    assert r.status_code == 200
    assert r.json()["tenant"]["oee_objetivo_pct"] == 78.5

    r2 = client.get("/accesos/mi-empresa/tenant")
    assert r2.status_code == 200
    assert r2.json()["oee_objetivo_pct"] == 78.5


def test_actualizar_objetivo_no_afecta_otros_campos_de_tenant(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r_antes = client.get("/accesos/mi-empresa/tenant")
    nombre_original = r_antes.json()["nombre"]

    r = client.patch("/accesos/mi-empresa/tenant", json={"oee_objetivo_pct": 90})
    assert r.status_code == 200
    assert r.json()["tenant"]["nombre"] == nombre_original

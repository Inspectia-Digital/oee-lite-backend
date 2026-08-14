"""Fase AZ (auditoría de frontend): Linea.metodo_calidad existe en el
modelo desde antes de este batch (default POR_RECHAZO) y ya lo consume
_calcular_metricas_oee (analytics.py), pero crear_linea nunca lo aceptaba
-- una línea nueva sólo podía terminar en "por_tiempo" con un PATCH
aparte después del alta. Gap real preexistente, cerrado acá junto con
umbral_calidad de MaestroSKU (ver test_sku_manual.py)."""
from app.models.domain import Planta
from tests.conftest import autenticar_como


def test_crear_linea_usa_default_por_rechazo(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta AZ")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea AZ"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201
    assert r.json()["metodo_calidad"] == "por_rechazo"


def test_crear_linea_con_metodo_calidad_explicito(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta AZ")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/lineas/",
        json={"nombre": "Línea AZ (calidad)", "metodo_calidad": "por_tiempo"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201
    assert r.json()["metodo_calidad"] == "por_tiempo"


def test_patch_linea_cambia_metodo_calidad(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta AZ")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    autenticar_como(gerente_a.id)
    creada = client.post(
        "/config/lineas/",
        json={"nombre": "Línea AZ"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    ).json()
    assert creada["metodo_calidad"] == "por_rechazo"

    r = client.patch(f"/config/lineas/{creada['id']}", json={"metodo_calidad": "por_tiempo"})
    assert r.status_code == 200
    assert r.json()["metodo_calidad"] == "por_tiempo"

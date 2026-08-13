"""QA-16 (auditoría QA): varios endpoints de analytics.py capturaban
CUALQUIER excepción y devolvían [] silenciosamente -- el frontend no
podía distinguir "sin datos" de "error real" (el mismo código servía
tanto para "no hay planta seleccionada aún" -- un estado legítimo --
como para un bug o una falla de base de datos real). Ahora sólo el
ValueError deliberado de validar_planta() (sin planta seleccionada) se
sigue tratando como "sin datos"; cualquier otra excepción propaga hasta
el manejador global (main.py), que responde 500 con un sobre JSON
consistente con el resto de la API -- nunca el mensaje/traceback crudo.
"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from app.models.domain import Linea, Planta
from tests.conftest import autenticar_como


def test_error_real_en_pareto_paradas_devuelve_500_no_lista_vacia(client, db, tenant_a, gerente_a):
    """raise_server_exceptions=False (el `client` compartido lo deja en
    True a propósito -- que un test rompa en serio siga apareciendo con
    traceback real, no como un 500 silencioso) -- acá se necesita un
    TestClient aparte para poder inspeccionar la RESPUESTA real que
    recibiría el frontend, que es lo que este test verifica."""
    planta = Planta(tenant_id=tenant_a, nombre="Planta QA-16")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea QA-16")
    db.add(linea)
    db.commit()

    autenticar_como(gerente_a.id)
    cliente_sin_raise = TestClient(app, raise_server_exceptions=False)
    with patch("app.routers.analytics.obtener_rango_dia", side_effect=RuntimeError("fallo simulado")):
        r = cliente_sin_raise.get(
            "/analytics/pareto-paradas/",
            headers={"X-Sub-Tenant-Id": str(planta.id)},
        )
    assert r.status_code == 500
    body = r.json()
    # Nunca se expone el mensaje real de la excepción ni un traceback.
    assert "fallo simulado" not in r.text
    assert "detail" in body
    assert "request_id" in body


def test_sin_planta_seleccionada_sigue_devolviendo_lista_vacia_no_error(client, db, tenant_a, gerente_a):
    """Regresión: el estado legítimo "sin planta todavía" (ValueError
    deliberado de validar_planta) no debe convertirse en 500 -- sigue
    siendo 200 con []."""
    autenticar_como(gerente_a.id)
    r = client.get("/analytics/pareto-paradas/")  # sin X-Sub-Tenant-Id
    assert r.status_code == 200
    assert r.json() == []

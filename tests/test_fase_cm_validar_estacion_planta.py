"""Fase CM (auditoría de frontend, P0-02): el frontend de Terminal
necesita saber a qué planta pertenece la estación de ESE dispositivo
para poder comparar contra los permisos del humano logueado en el
navegador de la terminal (ver TerminalPage.tsx) -- la credencial M2M ya
prueba que el dispositivo está autorizado; esto es aparte, para que la
UI no deje operar a un humano sin asignación real a esa planta."""
import uuid

from app.models.domain import Estacion, Linea, Planta
from tests.conftest import autenticar_como


def _crear_estacion_con_planta(db, tenant_id, nombre="Estación CM"):
    planta = Planta(tenant_id=tenant_id, nombre=f"Planta {nombre}")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre=f"Línea {nombre}")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(
        tenant_id=tenant_id, nombre=nombre, tipo="sensor", linea_id=linea.id,
        umbral_optimo=100, umbral_lento=150, umbral_alerta=200,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion, planta


def _emitir_api_key(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def test_validar_estacion_expone_planta_id(client, db, tenant_a, gerente_a):
    estacion, planta = _crear_estacion_con_planta(db, tenant_a)
    credencial = _emitir_api_key(client, gerente_a, estacion.id)

    r = client.get(
        f"/api/lite/estaciones/{estacion.id}/validar",
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 200
    assert r.json()["planta_id"] == str(planta.id)


def test_validar_estacion_planta_id_distingue_dos_plantas(client, db, tenant_a, gerente_a):
    """Dos estaciones de plantas distintas deben devolver planta_id distintos --
    el caso real que P0-02 necesita para poder comparar contra el permiso del
    humano logueado (¿tiene acceso a ESTA planta específica, o a otra?)."""
    estacion_a, planta_a = _crear_estacion_con_planta(db, tenant_a, "Planta X")
    estacion_b, planta_b = _crear_estacion_con_planta(db, tenant_a, "Planta Y")
    assert planta_a.id != planta_b.id

    cred_a = _emitir_api_key(client, gerente_a, estacion_a.id)
    cred_b = _emitir_api_key(client, gerente_a, estacion_b.id)

    r_a = client.get(f"/api/lite/estaciones/{estacion_a.id}/validar", headers={"X-Device-Key": cred_a})
    r_b = client.get(f"/api/lite/estaciones/{estacion_b.id}/validar", headers={"X-Device-Key": cred_b})

    assert r_a.json()["planta_id"] == str(planta_a.id)
    assert r_b.json()["planta_id"] == str(planta_b.id)
    assert r_a.json()["planta_id"] != r_b.json()["planta_id"]

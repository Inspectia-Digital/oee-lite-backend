"""Fase CM (auditoría de frontend, P0-02): el frontend de Terminal
necesita saber a qué planta pertenece la estación de ESE dispositivo
para poder comparar contra los permisos del humano logueado en el
navegador de la terminal (ver TerminalPage.tsx) -- la credencial M2M ya
prueba que el dispositivo está autorizado; esto es aparte, para que la
UI no deje operar a un humano sin asignación real a esa planta."""
import uuid
from datetime import time

from app.models.domain import Estacion, Linea, Planta, Turno
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


def test_validar_estacion_devuelve_el_contrato_que_espera_el_frontend(client, db, tenant_a, gerente_a):
    """Bug real (investigado a partir de un reporte externo): la respuesta
    traía `estacion_id`/`estacion_nombre` planos y `modo_asignacion_operarios`
    plano, sin `turnos` -- el frontend (EstacionValidada, oee-lite/src/types/
    api.ts) siempre esperó `id`/`nombre`, `configuracion.modo_asignacion_operarios`
    anidado y `turnos[]`, y no hay ninguna capa intermedia que traduzca. En
    producción real esto hacía que todo POST /scans mandara `id_estacion:
    undefined` (422) y que el paso de login de operario se saltara en
    silencio. Este test fija el contrato correcto."""
    estacion, planta = _crear_estacion_con_planta(db, tenant_a)
    turno = Turno(
        tenant_id=tenant_a, nombre="Mañana", hora_inicio=time(6, 0), hora_fin=time(14, 0),
        linea_id=estacion.linea_id, activo=True,
    )
    turno_inactivo = Turno(
        tenant_id=tenant_a, nombre="Discontinuado", hora_inicio=time(22, 0), hora_fin=time(6, 0),
        linea_id=estacion.linea_id, activo=False,
    )
    db.add(turno)
    db.add(turno_inactivo)
    db.commit()
    credencial = _emitir_api_key(client, gerente_a, estacion.id)

    r = client.get(
        f"/api/lite/estaciones/{estacion.id}/validar",
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 200
    body = r.json()

    assert body["id"] == str(estacion.id)
    assert body["nombre"] == estacion.nombre
    assert "estacion_id" not in body
    assert "estacion_nombre" not in body

    assert "configuracion" in body
    assert body["configuracion"]["modo_asignacion_operarios"]

    assert len(body["turnos"]) == 1
    assert body["turnos"][0]["id"] == str(turno.id)
    assert body["turnos"][0]["nombre"] == "Mañana"
    assert body["turnos"][0]["hora_inicio"] == "06:00"
    assert body["turnos"][0]["hora_fin"] == "14:00"

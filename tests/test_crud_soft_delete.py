"""CRUD y bajas lógicas: ausencia de hard-delete, incluir_inactivos,
reutilización de código tras baja lógica."""
import uuid

from sqlmodel import select

from app.models.domain import Planta, Operario, RolUsuario
from tests.conftest import autenticar_como, crear_usuario


def test_baja_de_planta_es_logica_no_fisica(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/accesos/mi-empresa/sub-tenants", json={"nombre": "Planta a borrar"})
    planta_id = r.json()["id"]

    client.delete(f"/accesos/mi-empresa/sub-tenants/{planta_id}")

    # La fila sigue existiendo en la base -- nunca se hizo DELETE físico.
    planta_db = db.get(Planta, uuid.UUID(planta_id))
    assert planta_db is not None
    assert planta_db.activo is False


def test_planta_desactivada_no_aparece_en_listado_normal(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/accesos/mi-empresa/sub-tenants", json={"nombre": "Planta oculta"})
    planta_id = r.json()["id"]
    client.delete(f"/accesos/mi-empresa/sub-tenants/{planta_id}")

    ids_normal = [p["id"] for p in client.get("/accesos/mi-empresa/sub-tenants").json()]
    assert planta_id not in ids_normal

    ids_con_inactivos = [p["id"] for p in client.get("/accesos/mi-empresa/sub-tenants?incluir_inactivos=true").json()]
    assert planta_id in ids_con_inactivos


def test_incluir_inactivos_requiere_gerencia_o_superadmin(client, db, tenant_a):
    operario = crear_usuario(db, tenant_a, RolUsuario.OPERARIO)
    autenticar_como(operario.id)
    r = client.get("/accesos/mi-empresa/sub-tenants?incluir_inactivos=true")
    assert r.status_code == 403


def test_reutilizar_legajo_tras_baja_logica_de_operario(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/config/operarios/", json={"legajo": "REUSE-001", "nombre_completo": "Primero"})
    assert r.status_code == 201
    primero_id = r.json()["id"]

    # No se puede duplicar mientras el primero sigue activo.
    r = client.post("/config/operarios/", json={"legajo": "REUSE-001", "nombre_completo": "Segundo"})
    assert r.status_code == 409

    client.delete(f"/config/operarios/{primero_id}")

    # Ahora sí, porque el legajo anterior ya no está activo.
    r = client.post("/config/operarios/", json={"legajo": "REUSE-001", "nombre_completo": "Segundo"})
    assert r.status_code == 201

    # Ambas filas siguen existiendo en la base (ninguna se borró físicamente).
    filas = db.exec(select(Operario).where(Operario.tenant_id == tenant_a, Operario.legajo == "REUSE-001")).all()
    assert len(filas) == 2


def test_endpoint_de_estacion_ya_no_hace_hard_delete(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r_planta = client.post("/accesos/mi-empresa/sub-tenants", json={"nombre": "Planta E"})
    r_linea = client.post("/config/lineas/", json={"nombre": "Linea E", "planta_id": r_planta.json()["id"]})
    r_estacion = client.post("/config/estaciones/", json={"nombre": "Estacion E", "tipo": "sensor", "linea_id": r_linea.json()["id"]})
    estacion_id = r_estacion.json()["id"]

    r_delete = client.delete(f"/config/estaciones/{estacion_id}")
    assert r_delete.status_code == 200

    # Si hubiera sido hard-delete, este GET devolvería 404. Sigue existiendo (activa=False).
    r_get = client.get(f"/config/estaciones/{estacion_id}")
    assert r_get.status_code == 200
    assert r_get.json()["activa"] is False

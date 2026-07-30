"""Fase I: oee-cascada, rendimiento-secuencial, reporte-produccion, command-center/summary."""
import uuid
from datetime import date, datetime, timedelta, timezone

from app.models.domain import Estacion, Linea, Planta, RolUsuario, Turno
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta I")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea I")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(
        tenant_id=tenant_id, nombre="Estación I", tipo="sensor", linea_id=linea.id,
        umbral_optimo=100, posicion_linea=1, activa=True,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_id, nombre="Full", hora_inicio="00:00:00", hora_fin="23:59:00", linea_id=linea.id)
    db.add(turno)
    db.commit()

    return planta, linea, estacion


def _emitir_key_y_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _emitir_evento(client, credencial, estacion_id):
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201


# ---------- oee-cascada ----------

def test_cascada_sin_planta_devuelve_ceros(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-cascada/")
    assert r.status_code == 200
    assert r.json()["tiempo_calendario_min"] == 0.0


def test_cascada_etapas_decrecen_monotonicamente(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)
    _emitir_evento(client, credencial, estacion.id)

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-cascada/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    c = r.json()
    assert c["tiempo_calendario_min"] >= c["tiempo_planificado_min"]
    assert c["tiempo_planificado_min"] >= c["tiempo_operativo_min"]
    assert c["tiempo_operativo_min"] >= c["tiempo_neto_min"]
    assert c["tiempo_neto_min"] >= c["tiempo_efectivo_min"]
    assert c["tiempo_efectivo_min"] >= 0


# ---------- rendimiento-secuencial ----------

def test_rendimiento_secuencial_ordenado_por_posicion(client, db, tenant_a, gerente_a):
    planta, linea, estacion1 = _preparar_escenario(db, tenant_a)
    estacion2 = Estacion(tenant_id=tenant_a, nombre="Estación I2", tipo="sensor", linea_id=linea.id, umbral_optimo=50, posicion_linea=2)
    db.add(estacion2)
    db.commit()
    db.refresh(estacion2)

    cred1 = _emitir_key_y_credencial(client, gerente_a, estacion1.id)
    cred2 = _emitir_key_y_credencial(client, gerente_a, estacion2.id)
    _emitir_evento(client, cred1, estacion1.id)
    _emitir_evento(client, cred2, estacion2.id)

    autenticar_como(gerente_a.id)
    r = client.get(f"/analytics/rendimiento-secuencial/?linea_id={linea.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    filas = r.json()
    assert [f["posicion_linea"] for f in filas] == sorted(f["posicion_linea"] for f in filas)


# ---------- reporte-produccion ----------

def test_reporte_produccion_filas_planas_por_fecha(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)
    _emitir_evento(client, credencial, estacion.id)

    autenticar_como(gerente_a.id)
    hoy = date.today().isoformat()
    r = client.get(
        f"/analytics/reporte-produccion/?fecha_desde={hoy}&fecha_hasta={hoy}",
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["estacion"] == "Estación I"
    assert filas[0]["total_piezas"] == 1


def test_reporte_produccion_rechaza_rango_invertido(client, db, tenant_a, gerente_a):
    planta, _, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/reporte-produccion/?fecha_desde=2026-01-10&fecha_hasta=2026-01-01",
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


# ---------- command-center/summary ----------

def test_command_center_gerencia_ve_todas_las_plantas(client, db, tenant_a, gerente_a):
    planta1, _, estacion1 = _preparar_escenario(db, tenant_a)
    planta2 = Planta(tenant_id=tenant_a, nombre="Planta I2")
    db.add(planta2)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get("/command-center/summary")
    assert r.status_code == 200
    data = r.json()
    nombres = {p["nombre"] for p in data["plantas"]}
    assert {"Planta I", "Planta I2"}.issubset(nombres)
    assert data["infraestructura"]["estaciones_total"] >= 1


def test_command_center_supervisor_solo_ve_su_planta(client, db, tenant_a):
    planta1, _, _ = _preparar_escenario(db, tenant_a)
    planta2 = Planta(tenant_id=tenant_a, nombre="Planta I2")
    db.add(planta2)
    db.commit()
    db.refresh(planta2)

    from app.models.domain import UsuarioPlanta
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=supervisor.id, planta_id=planta1.id))
    db.commit()

    autenticar_como(supervisor.id)
    r = client.get("/command-center/summary")
    assert r.status_code == 200
    nombres = {p["nombre"] for p in r.json()["plantas"]}
    assert nombres == {"Planta I"}


def test_command_center_sin_plantas_asignadas_devuelve_vacio(client, db, tenant_a):
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    autenticar_como(supervisor.id)
    r = client.get("/command-center/summary")
    assert r.status_code == 200
    assert r.json()["plantas"] == []
    assert r.json()["oee_global"] is None

"""Fase CC (FE-P0-08, auditoría de robustez, batch 3): workflow de falso
positivo -- proponer y aprobar la exclusión de una parada del cálculo de
OEE. Cubre el CRUD del workflow (proponer/resolver-exclusion-oee),
RBAC (quién propone vs. quién aprueba, bloqueo de auto-aprobación),
el filtro de la cola de aprobación en /supervisor/paradas, y el efecto
real sobre el cálculo de OEE (_calcular_metricas_oee, analytics.py)."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import (
    Estacion, EstadoParada, Linea, MotivoParada, Planta,
    ParadaDetectada, RolUsuario, TipoParada, UsuarioPlanta,
)
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Exclusion OEE")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(
        tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Exclusion OEE",
        tiempo_ideal_seg=100, tiempo_lento_seg=150, tiempo_alerta_seg=200,
    )
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación Exclusion OEE", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def _usuario_con_planta(db, tenant_id, rol, planta_id):
    usuario = crear_usuario(db, tenant_id, rol)
    db.add(UsuarioPlanta(tenant_id=tenant_id, usuario_id=usuario.id, planta_id=planta_id, activo=True))
    db.commit()
    return usuario


def _crear_parada(db, tenant_id, estacion_id, *, estado=EstadoParada.CLASIFICADA, duracion=1000.0, motivo_fk=None):
    parada = ParadaDetectada(
        tenant_id=tenant_id, estacion_fk=estacion_id, motivo_fk=motivo_fk,
        inicio=datetime.now(timezone.utc).replace(tzinfo=None), duracion_segundos=duracion,
        estado=estado, origen="AUTOMATICA",
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)
    return parada


# ---------- proponer-exclusion-oee ----------

def test_encargado_puede_proponer_exclusion(client, db, tenant_a):
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.post(
        f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee",
        json={"motivo": "Corte de red del sensor, no fue una parada real."},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["exclusion_oee"] == "propuesta"
    assert body["exclusion_propuesta_por_id"] == str(encargado.id)
    assert body["exclusion_propuesta_at"] is not None


def test_no_se_puede_proponer_dos_veces(client, db, tenant_a):
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    r1 = client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)
    assert r1.status_code == 200

    r2 = client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "De nuevo."}, headers=headers)
    assert r2.status_code == 409


def test_no_se_puede_proponer_sobre_una_ya_aprobada(client, db, tenant_a):
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    gerente = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(encargado.id)
    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)

    autenticar_como(gerente.id)
    r_aprobar = client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": True}, headers=headers)
    assert r_aprobar.status_code == 200

    autenticar_como(encargado.id)
    r = client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Otra vez."}, headers=headers)
    assert r.status_code == 409


def test_proponer_exclusion_requiere_motivo_minimo(client, db, tenant_a):
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.post(
        f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee",
        json={"motivo": "x"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 422


# ---------- resolver-exclusion-oee ----------

def test_gerencia_puede_aprobar_exclusion_propuesta_por_otro(client, db, tenant_a):
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    gerente = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(encargado.id)
    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)

    autenticar_como(gerente.id)
    r = client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": True, "nota": "Confirmado con el operario."}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["exclusion_oee"] == "aprobada"
    assert body["exclusion_resuelta_por_id"] == str(gerente.id)
    assert body["exclusion_resolucion_nota"] == "Confirmado con el operario."


def test_gerencia_puede_rechazar_exclusion(client, db, tenant_a):
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    gerente = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(encargado.id)
    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)

    autenticar_como(gerente.id)
    r = client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": False}, headers=headers)
    assert r.status_code == 200
    assert r.json()["exclusion_oee"] == "rechazada"


def test_produccion_puede_aprobar_exclusion_propuesta_por_otro(client, db, tenant_a):
    """Fase CP (auditoría de frontend, P0-05): Producción se agrega a la
    matriz de aprobadores -- confirmado con el usuario, la restricción a
    sólo Gerencia/SuperAdmin de Fase CC era más angosta de lo que el
    negocio quería."""
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    produccion = _usuario_con_planta(db, tenant_a, RolUsuario.PRODUCCION, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(encargado.id)
    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)

    autenticar_como(produccion.id)
    r = client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": True}, headers=headers)
    assert r.status_code == 200
    assert r.json()["exclusion_oee"] == "aprobada"
    assert r.json()["exclusion_resuelta_por_id"] == str(produccion.id)


def test_produccion_no_puede_autoaprobar_la_propia_propuesta(client, db, tenant_a):
    """Mismo control de dos personas que ya vale para Gerencia -- Producción
    tampoco puede aprobar su propia propuesta."""
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    produccion = _usuario_con_planta(db, tenant_a, RolUsuario.PRODUCCION, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(produccion.id)
    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)

    r = client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": True}, headers=headers)
    assert r.status_code == 403


def test_supervisor_no_puede_aprobar_exclusion(client, db, tenant_a):
    """ROLES_APROBACION_EXCLUSION_OEE es más angosto que ROLES_SUPERVISION_COMPLETA
    a propósito -- Supervisor puede proponer, no aprobar."""
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    supervisor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(supervisor.id)
    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)

    r = client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": True}, headers=headers)
    assert r.status_code == 403


def test_no_se_puede_autoaprobar_la_propia_propuesta(client, db, tenant_a):
    """La propiedad real que "control de dos personas" busca -- ni
    siquiera Gerencia puede aprobar su propia propuesta."""
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    gerente = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(gerente.id)
    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)

    r = client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": True}, headers=headers)
    assert r.status_code == 403


def test_no_se_puede_resolver_sin_propuesta_pendiente(client, db, tenant_a):
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)  # exclusion_oee = "ninguna"
    gerente = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    autenticar_como(gerente.id)

    r = client.post(
        f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee",
        json={"aprobar": True},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 409


# ---------- listar_historial_paradas: filtro de cola + nombres resueltos ----------

def test_historial_filtra_por_exclusion_oee_propuesta(client, db, tenant_a):
    planta, _, estacion = _preparar_escenario(db, tenant_a)
    parada_propuesta = _crear_parada(db, tenant_a, estacion.id)
    _crear_parada(db, tenant_a, estacion.id)  # sin proponer -- no debe aparecer en el filtro
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(encargado.id)
    client.post(f"/supervisor/paradas/{parada_propuesta.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo."}, headers=headers)

    r = client.get("/supervisor/paradas?exclusion_oee=propuesta", headers=headers)
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["id"] == str(parada_propuesta.id)
    assert filas[0]["exclusion_propuesta_por_nombre"] is not None


# ---------- efecto real sobre el cálculo de OEE ----------

def test_exclusion_aprobada_saca_la_parada_del_calculo_de_oee(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=gerente_a.id, planta_id=planta.id, activo=True))
    db.commit()
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    # Un evento real (tiempo_ideal_seg=100) para que tiempo_ideal_total > 0.
    autenticar_como(gerente_a.id)
    credencial = client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)}).json()["credencial_completa"]
    r_scan = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r_scan.status_code == 201

    # Parada NO planificada de 1000s -- domina el cálculo (ideal=100s).
    parada = _crear_parada(db, tenant_a, estacion.id, duracion=1000.0)

    r_antes = client.get("/analytics/oee-general/", headers=headers)
    assert r_antes.status_code == 200
    disp_antes = r_antes.json()["disponibilidad_pct"]
    # tiempo_planificado_neto = 100 (ideal) + 1000 (parada) = 1100
    # tiempo_operativo = 1100 - 1000 = 100 -> disponibilidad = 100/1100 ≈ 9.1%
    assert disp_antes < 15.0

    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Falso positivo -- corte de red."}, headers=headers)

    gerente_b = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    autenticar_como(gerente_b.id)
    r_aprobar = client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": True}, headers=headers)
    assert r_aprobar.status_code == 200

    r_despues = client.get("/analytics/oee-general/", headers=headers)
    disp_despues = r_despues.json()["disponibilidad_pct"]
    # Sin la parada (excluida), tiempo_operativo == tiempo_planificado -> 100%.
    assert disp_despues == 100.0
    assert disp_despues > disp_antes


def test_exclusion_rechazada_sigue_contando_en_oee(client, db, tenant_a, gerente_a):
    """Contraste con el test anterior -- RECHAZADA no es APROBADA, la
    parada sigue pesando en Disponibilidad exactamente igual que antes."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=gerente_a.id, planta_id=planta.id, activo=True))
    db.commit()
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(gerente_a.id)
    credencial = client.post("/config/api-keys/", json={"estacion_id": str(estacion.id)}).json()["credencial_completa"]
    client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    parada = _crear_parada(db, tenant_a, estacion.id, duracion=1000.0)

    disp_antes = client.get("/analytics/oee-general/", headers=headers).json()["disponibilidad_pct"]

    client.post(f"/supervisor/paradas/{parada.id}/proponer-exclusion-oee", json={"motivo": "Duda."}, headers=headers)
    gerente_b = _usuario_con_planta(db, tenant_a, RolUsuario.GERENCIA, planta.id)
    autenticar_como(gerente_b.id)
    client.post(f"/supervisor/paradas/{parada.id}/resolver-exclusion-oee", json={"aprobar": False}, headers=headers)

    disp_despues = client.get("/analytics/oee-general/", headers=headers).json()["disponibilidad_pct"]
    assert disp_despues == disp_antes


# ---------- aislamiento multi-tenant ----------

def test_no_se_puede_proponer_exclusion_de_parada_de_otro_tenant(client, db, tenant_a, tenant_b):
    planta_a, _, estacion_a = _preparar_escenario(db, tenant_a)
    parada_a = _crear_parada(db, tenant_a, estacion_a.id)

    encargado_b = crear_usuario(db, tenant_b, RolUsuario.ENCARGADO)
    autenticar_como(encargado_b.id)

    r = client.post(
        f"/supervisor/paradas/{parada_a.id}/proponer-exclusion-oee",
        json={"motivo": "Intento cruzado."},
    )
    assert r.status_code in (400, 404)

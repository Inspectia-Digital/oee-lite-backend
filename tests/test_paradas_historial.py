"""Fase N (auditoría de producción del front, ítems #8 y #9): historial
completo de paradas. Antes sólo existía /paradas-pendientes (estado
PENDIENTE); ni el historial de clasificadas ni el listado de programadas
tenían un GET real -- el front las leía de un mock local en memoria.

Cubre: GET /supervisor/paradas (filtros + paginación) y
DELETE /supervisor/paradas/{id} (sólo paradas PLANIFICADA que no
empezaron todavía)."""
from datetime import datetime, timedelta, timezone

from app.models.domain import Estacion, EstadoParada, Linea, MotivoParada, ParadaDetectada, Planta, TipoParada
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta N")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea N")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación N", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    motivo_planificado = MotivoParada(tenant_id=tenant_id, nombre="Mantenimiento", tipo_parada=TipoParada.PLANIFICADA)
    motivo_no_planificado = MotivoParada(tenant_id=tenant_id, nombre="Falla eléctrica", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(motivo_planificado)
    db.add(motivo_no_planificado)
    db.commit()
    db.refresh(motivo_planificado)
    db.refresh(motivo_no_planificado)

    return planta, linea, estacion, motivo_planificado, motivo_no_planificado


def _crear_parada(db, tenant_id, estacion_id, motivo_fk, estado, origen, inicio):
    p = ParadaDetectada(
        tenant_id=tenant_id, estacion_fk=estacion_id, motivo_fk=motivo_fk,
        inicio=inicio, fin=inicio + timedelta(minutes=10), duracion_segundos=600.0,
        estado=estado, origen=origen,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ---------- GET /supervisor/paradas ----------

def test_historial_incluye_clasificadas_y_planificadas(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo_plan, motivo_no_plan = _preparar_escenario(db, tenant_a)
    ahora = datetime.utcnow()
    _crear_parada(db, tenant_a, estacion.id, motivo_no_plan.id, EstadoParada.CLASIFICADA, "AUTOMATICA", ahora - timedelta(days=1))
    _crear_parada(db, tenant_a, estacion.id, motivo_plan.id, EstadoParada.CLASIFICADA, "PLANIFICADA", ahora - timedelta(hours=2))

    autenticar_como(gerente_a.id)
    r = client.get("/supervisor/paradas", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_historial_filtra_por_origen(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo_plan, motivo_no_plan = _preparar_escenario(db, tenant_a)
    ahora = datetime.utcnow()
    _crear_parada(db, tenant_a, estacion.id, motivo_no_plan.id, EstadoParada.CLASIFICADA, "AUTOMATICA", ahora - timedelta(days=1))
    _crear_parada(db, tenant_a, estacion.id, motivo_plan.id, EstadoParada.CLASIFICADA, "PLANIFICADA", ahora - timedelta(hours=2))

    autenticar_como(gerente_a.id)
    r = client.get("/supervisor/paradas", params={"origen": "PLANIFICADA"}, headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["origen"] == "PLANIFICADA"
    assert filas[0]["motivo_nombre"] == "Mantenimiento"


def test_historial_filtra_por_estado_y_rango_de_fechas(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo_plan, motivo_no_plan = _preparar_escenario(db, tenant_a)
    ahora = datetime.utcnow()
    _crear_parada(db, tenant_a, estacion.id, None, EstadoParada.PENDIENTE, "AUTOMATICA", ahora)
    _crear_parada(db, tenant_a, estacion.id, motivo_no_plan.id, EstadoParada.CLASIFICADA, "AUTOMATICA", ahora - timedelta(days=10))

    autenticar_como(gerente_a.id)
    hoy = ahora.date().isoformat()
    r = client.get(
        "/supervisor/paradas",
        params={"estado": "clasificada", "fecha_desde": hoy, "fecha_hasta": hoy},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json() == []  # la única clasificada está fuera del rango de fechas


def test_historial_respeta_paginacion(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo_plan, motivo_no_plan = _preparar_escenario(db, tenant_a)
    ahora = datetime.utcnow()
    for i in range(5):
        _crear_parada(db, tenant_a, estacion.id, motivo_no_plan.id, EstadoParada.CLASIFICADA, "AUTOMATICA", ahora - timedelta(hours=i))

    autenticar_como(gerente_a.id)
    r = client.get("/supervisor/paradas", params={"limit": 2}, headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_historial_no_filtra_otro_tenant(client, db, tenant_a, tenant_b, gerente_a):
    planta_a, linea_a, estacion_a, motivo_a, _ = _preparar_escenario(db, tenant_a)
    planta_b, linea_b, estacion_b, motivo_b, _ = _preparar_escenario(db, tenant_b)
    ahora = datetime.utcnow()
    _crear_parada(db, tenant_a, estacion_a.id, motivo_a.id, EstadoParada.CLASIFICADA, "AUTOMATICA", ahora)
    _crear_parada(db, tenant_b, estacion_b.id, motivo_b.id, EstadoParada.CLASIFICADA, "AUTOMATICA", ahora)

    autenticar_como(gerente_a.id)
    r = client.get("/supervisor/paradas", headers={"X-Sub-Tenant-Id": str(planta_a.id)})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_historial_limit_invalido_devuelve_400(client, db, tenant_a, gerente_a):
    planta, *_ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get("/supervisor/paradas", params={"limit": 1000}, headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 400


# ---------- DELETE /supervisor/paradas/{id} ----------

def test_eliminar_planificada_futura_funciona(client, db, tenant_a, gerente_a):
    """Fase DU (P0-05 revisado): soft-delete -- la fila NO desaparece del
    historial, queda con estado=ANULADA (antes era hard-delete real,
    r2.json() == [])."""
    planta, linea, estacion, motivo_plan, _ = _preparar_escenario(db, tenant_a)
    futuro = datetime.utcnow() + timedelta(days=1)
    parada = _crear_parada(db, tenant_a, estacion.id, motivo_plan.id, EstadoParada.CLASIFICADA, "PLANIFICADA", futuro)

    autenticar_como(gerente_a.id)
    # TestClient.delete() no acepta `json=` (httpx restringe el shortcut a
    # sólo params de URL/headers para DELETE) -- .request() sí es genérico.
    r = client.request(
        "DELETE", f"/supervisor/paradas/{parada.id}",
        json={"motivo": "Se canceló el cambio de formato"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200

    r2 = client.get("/supervisor/paradas", headers={"X-Sub-Tenant-Id": str(planta.id)})
    filas = r2.json()
    assert len(filas) == 1
    assert filas[0]["estado"] == "anulada"
    assert filas[0]["motivo_anulacion"] == "Se canceló el cambio de formato"
    assert filas[0]["anulada_por_id"] == str(gerente_a.id)
    assert filas[0]["anulada_at"] is not None


def test_eliminar_planificada_sin_motivo_funciona(client, db, tenant_a, gerente_a):
    """El motivo es opcional -- DELETE sin body sigue funcionando."""
    planta, linea, estacion, motivo_plan, _ = _preparar_escenario(db, tenant_a)
    futuro = datetime.utcnow() + timedelta(days=1)
    parada = _crear_parada(db, tenant_a, estacion.id, motivo_plan.id, EstadoParada.CLASIFICADA, "PLANIFICADA", futuro)

    autenticar_como(gerente_a.id)
    r = client.delete(f"/supervisor/paradas/{parada.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200

    r2 = client.get("/supervisor/paradas", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r2.json()[0]["motivo_anulacion"] is None


def test_eliminar_planificada_ya_anulada_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo_plan, _ = _preparar_escenario(db, tenant_a)
    futuro = datetime.utcnow() + timedelta(days=1)
    parada = _crear_parada(db, tenant_a, estacion.id, motivo_plan.id, EstadoParada.ANULADA, "PLANIFICADA", futuro)

    autenticar_como(gerente_a.id)
    r = client.delete(f"/supervisor/paradas/{parada.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 409


def test_eliminar_planificada_que_ya_empezo_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo_plan, _ = _preparar_escenario(db, tenant_a)
    pasado = datetime.utcnow() - timedelta(hours=1)
    parada = _crear_parada(db, tenant_a, estacion.id, motivo_plan.id, EstadoParada.CLASIFICADA, "PLANIFICADA", pasado)

    autenticar_como(gerente_a.id)
    r = client.delete(f"/supervisor/paradas/{parada.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 409


def test_eliminar_parada_automatica_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, estacion, _, motivo_no_plan = _preparar_escenario(db, tenant_a)
    futuro = datetime.utcnow() + timedelta(days=1)
    parada = _crear_parada(db, tenant_a, estacion.id, motivo_no_plan.id, EstadoParada.CLASIFICADA, "AUTOMATICA", futuro)

    autenticar_como(gerente_a.id)
    r = client.delete(f"/supervisor/paradas/{parada.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 409


def test_eliminar_parada_inexistente_devuelve_404(client, db, tenant_a, gerente_a):
    planta, *_ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.delete(
        "/supervisor/paradas/00000000-0000-0000-0000-000000000000",
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 404


def test_eliminar_parada_de_otro_tenant_devuelve_404(client, db, tenant_a, tenant_b, gerente_a, gerente_b):
    planta_a, linea_a, estacion_a, motivo_a, _ = _preparar_escenario(db, tenant_a)
    planta_b, linea_b, estacion_b, motivo_b, _ = _preparar_escenario(db, tenant_b)
    futuro = datetime.utcnow() + timedelta(days=1)
    parada_b = _crear_parada(db, tenant_b, estacion_b.id, motivo_b.id, EstadoParada.CLASIFICADA, "PLANIFICADA", futuro)

    autenticar_como(gerente_a.id)
    r = client.delete(f"/supervisor/paradas/{parada_b.id}", headers={"X-Sub-Tenant-Id": str(planta_a.id)})
    assert r.status_code == 404


# ---------- Registrar planificada ahora marca origen ----------

def test_registrar_planificada_setea_origen(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo_plan, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    inicio = (datetime.utcnow() + timedelta(days=1)).isoformat()
    fin = (datetime.utcnow() + timedelta(days=1, hours=1)).isoformat()
    r = client.post(
        "/supervisor/paradas/planificadas",
        json={"estacion_fk": str(estacion.id), "motivo_fk": str(motivo_plan.id), "inicio": inicio, "fin": fin},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["origen"] == "PLANIFICADA"

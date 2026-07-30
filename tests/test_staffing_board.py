"""Fase H: tablero de dotación, monitor de eventos live y asignación de
supervisores por día."""
import uuid
from datetime import date

from sqlmodel import select

from app.models.domain import (
    AsignacionSupervisor, AsignacionTurno, Estacion, Linea, LiteEventoProduccion,
    Operario, Planta, RolUsuario, Supervisor, Turno,
)
from tests.conftest import autenticar_como, crear_usuario


def _armar_planta_completa(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta H")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea H")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación H", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_id, nombre="Mañana", hora_inicio="06:00", hora_fin="14:00", linea_id=linea.id)
    db.add(turno)
    db.commit()
    db.refresh(turno)

    operario = Operario(tenant_id=tenant_id, legajo="OP-H1", nombre_completo="Operario H")
    db.add(operario)
    db.commit()
    db.refresh(operario)

    supervisor = Supervisor(tenant_id=tenant_id, legajo="SUP-H1", nombre_completo="Supervisor H")
    db.add(supervisor)
    db.commit()
    db.refresh(supervisor)

    return planta, linea, estacion, turno, operario, supervisor


def _supervisor_asignado(db, tenant_id, planta_id):
    from app.models.domain import UsuarioPlanta
    sup_usuario = crear_usuario(db, tenant_id, RolUsuario.SUPERVISOR)
    db.add(UsuarioPlanta(tenant_id=tenant_id, usuario_id=sup_usuario.id, planta_id=planta_id))
    db.commit()
    return sup_usuario


# ---------- Tablero de dotación (operario ↔ estación) ----------

def test_asignar_dotacion_y_listar(client, db, tenant_a):
    planta, linea, estacion, turno, operario, _ = _armar_planta_completa(db, tenant_a)
    sup_usuario = _supervisor_asignado(db, tenant_a, planta.id)
    autenticar_como(sup_usuario.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    hoy = date.today().isoformat()
    r = client.post(
        "/supervisor/asignaciones/",
        json={"fecha": hoy, "estacion_fk": str(estacion.id), "operario_fk": str(operario.id), "turno_fk": str(turno.id)},
        headers=headers,
    )
    assert r.status_code == 201

    r = client.get(f"/supervisor/asignaciones/?fecha={hoy}&linea_id={linea.id}", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["operario_fk"] == str(operario.id)


def test_reasignar_dotacion_sobrescribe_no_duplica(client, db, tenant_a):
    planta, linea, estacion, turno, operario, _ = _armar_planta_completa(db, tenant_a)
    otro_operario = Operario(tenant_id=tenant_a, legajo="OP-H2", nombre_completo="Otro")
    db.add(otro_operario)
    db.commit()
    db.refresh(otro_operario)

    sup_usuario = _supervisor_asignado(db, tenant_a, planta.id)
    autenticar_como(sup_usuario.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    hoy = date.today().isoformat()
    payload = {"fecha": hoy, "estacion_fk": str(estacion.id), "turno_fk": str(turno.id)}

    client.post("/supervisor/asignaciones/", json={**payload, "operario_fk": str(operario.id)}, headers=headers)
    r = client.post("/supervisor/asignaciones/", json={**payload, "operario_fk": str(otro_operario.id)}, headers=headers)
    assert r.status_code == 201

    filas = db.exec(select(AsignacionTurno).where(AsignacionTurno.tenant_id == tenant_a)).all()
    assert len(filas) == 1
    assert filas[0].operario_fk == otro_operario.id


def test_liberar_estacion_borra_la_fila(client, db, tenant_a):
    planta, linea, estacion, turno, operario, _ = _armar_planta_completa(db, tenant_a)
    sup_usuario = _supervisor_asignado(db, tenant_a, planta.id)
    autenticar_como(sup_usuario.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    hoy = date.today().isoformat()

    creado = client.post(
        "/supervisor/asignaciones/",
        json={"fecha": hoy, "estacion_fk": str(estacion.id), "operario_fk": str(operario.id), "turno_fk": str(turno.id)},
        headers=headers,
    ).json()

    r = client.delete(f"/supervisor/asignaciones/{creado['id']}", headers=headers)
    assert r.status_code == 200
    assert db.get(AsignacionTurno, uuid.UUID(creado["id"])) is None


def test_dotacion_sin_planta_seleccionada_devuelve_400(client, db, tenant_a):
    _, linea, _, _, _, _ = _armar_planta_completa(db, tenant_a)
    sup_usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    autenticar_como(sup_usuario.id)
    hoy = date.today().isoformat()
    r = client.get(f"/supervisor/asignaciones/?fecha={hoy}&linea_id={linea.id}")
    assert r.status_code == 400


# ---------- Monitor de eventos en vivo ----------

def test_eventos_live_ordenados_desc_con_operario_resuelto(client, db, tenant_a):
    planta, linea, estacion, turno, operario, _ = _armar_planta_completa(db, tenant_a)
    sup_usuario = _supervisor_asignado(db, tenant_a, planta.id)

    db.add(AsignacionTurno(tenant_id=tenant_a, fecha=date.today(), estacion_fk=estacion.id, operario_fk=operario.id, turno_fk=turno.id))
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(estacion.id), estado="OPTIMO"))
    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(estacion.id), estado="LENTO"))
    db.commit()

    autenticar_como(sup_usuario.id)
    r = client.get("/supervisor/eventos/live", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    eventos = r.json()
    assert len(eventos) == 2
    assert eventos[0]["timestamp"] >= eventos[1]["timestamp"]
    assert eventos[0]["operario_id"] == str(operario.id)


def test_eventos_live_no_cruza_plantas(client, db, tenant_a):
    planta1, linea1, estacion1, _, _, _ = _armar_planta_completa(db, tenant_a)
    planta2 = Planta(tenant_id=tenant_a, nombre="Planta H2")
    db.add(planta2)
    db.commit()
    db.refresh(planta2)
    linea2 = Linea(tenant_id=tenant_a, planta_id=planta2.id, nombre="Línea H2")
    db.add(linea2)
    db.commit()
    db.refresh(linea2)
    estacion2 = Estacion(tenant_id=tenant_a, nombre="Estación H2", tipo="sensor", linea_id=linea2.id)
    db.add(estacion2)
    db.commit()
    db.refresh(estacion2)

    db.add(LiteEventoProduccion(tenant_id=tenant_a, id_estacion=str(estacion2.id), estado="OPTIMO"))
    db.commit()

    sup_usuario = _supervisor_asignado(db, tenant_a, planta1.id)
    autenticar_como(sup_usuario.id)
    r = client.get("/supervisor/eventos/live", headers={"X-Sub-Tenant-Id": str(planta1.id)})
    assert r.status_code == 200
    assert r.json() == []


# ---------- Asignación de supervisores por día ----------

def test_asignar_supervisor_y_listar(client, db, tenant_a):
    planta, linea, _, turno, _, supervisor = _armar_planta_completa(db, tenant_a)
    sup_usuario = _supervisor_asignado(db, tenant_a, planta.id)
    autenticar_como(sup_usuario.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    hoy = date.today().isoformat()

    r = client.post(
        "/asignaciones/supervisor/",
        json={"fecha": hoy, "linea_id": str(linea.id), "turno_id": str(turno.id), "supervisor_id": str(supervisor.id)},
        headers=headers,
    )
    assert r.status_code == 201

    r = client.get(f"/asignaciones/supervisor/?fecha={hoy}&linea_id={linea.id}", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["supervisor_id"] == str(supervisor.id)


def test_reasignar_supervisor_sobrescribe(client, db, tenant_a):
    planta, linea, _, turno, _, supervisor = _armar_planta_completa(db, tenant_a)
    otro_supervisor = Supervisor(tenant_id=tenant_a, legajo="SUP-H2", nombre_completo="Otro Sup")
    db.add(otro_supervisor)
    db.commit()
    db.refresh(otro_supervisor)

    sup_usuario = _supervisor_asignado(db, tenant_a, planta.id)
    autenticar_como(sup_usuario.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    hoy = date.today().isoformat()
    payload = {"fecha": hoy, "linea_id": str(linea.id), "turno_id": str(turno.id)}

    client.post("/asignaciones/supervisor/", json={**payload, "supervisor_id": str(supervisor.id)}, headers=headers)
    r = client.post("/asignaciones/supervisor/", json={**payload, "supervisor_id": str(otro_supervisor.id)}, headers=headers)
    assert r.status_code == 201

    filas = db.exec(select(AsignacionSupervisor).where(AsignacionSupervisor.tenant_id == tenant_a)).all()
    assert len(filas) == 1
    assert filas[0].supervisor_id == otro_supervisor.id

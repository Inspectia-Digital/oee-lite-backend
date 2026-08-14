"""Fase BZ (auditoría de robustez, batch 3): POST /api/lite/operario/logout
formaliza [SALIR] -- antes ese botón de la terminal sólo reseteaba estado
LOCAL del front, ningún registro quedaba del lado del backend."""
import uuid
from datetime import time

from sqlmodel import select

from app.models.domain import AsignacionTurno, Estacion, Linea, Operario, Planta, Turno
from tests.conftest import autenticar_como


def _crear_estacion_con_planta(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Logout")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Logout")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(
        tenant_id=tenant_id, nombre="Estación Logout", tipo="sensor", linea_id=linea.id,
        umbral_optimo=100, umbral_lento=150, umbral_alerta=200,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion


def _emitir_api_key(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _crear_turno_y_operario(db, tenant_id):
    turno = Turno(tenant_id=tenant_id, nombre="Turno Logout", hora_inicio=time(6, 0), hora_fin=time(14, 0))
    db.add(turno)
    db.commit()
    db.refresh(turno)
    operario = Operario(tenant_id=tenant_id, legajo=f"LEG-{uuid.uuid4().hex[:6]}", nombre_completo="Operario Logout")
    db.add(operario)
    db.commit()
    db.refresh(operario)
    return turno, operario


def test_logout_marca_hora_salida_en_la_asignacion_del_dia(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    turno, operario = _crear_turno_y_operario(db, tenant_a)

    r_login = client.post(
        "/api/lite/operario/login",
        json={"legajo": operario.legajo, "turno_fk": str(turno.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r_login.status_code == 200
    asignacion_id = r_login.json()["asignacion_id"]

    r_logout = client.post(
        "/api/lite/operario/logout",
        json={"turno_fk": str(turno.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r_logout.status_code == 200
    assert r_logout.json()["asignacion_id"] == asignacion_id

    fila = db.get(AsignacionTurno, uuid.UUID(asignacion_id))
    assert fila.hora_salida is not None


def test_logout_sin_login_previo_devuelve_404(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    turno_id = uuid.uuid4()

    r = client.post(
        "/api/lite/operario/logout",
        json={"turno_fk": str(turno_id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 404


def test_login_de_nuevo_operario_limpia_hora_salida_anterior(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    turno, operario_1 = _crear_turno_y_operario(db, tenant_a)
    operario_2 = Operario(tenant_id=tenant_a, legajo=f"LEG-{uuid.uuid4().hex[:6]}", nombre_completo="Operario Dos")
    db.add(operario_2)
    db.commit()
    db.refresh(operario_2)

    client.post(
        "/api/lite/operario/login",
        json={"legajo": operario_1.legajo, "turno_fk": str(turno.id)},
        headers={"X-Device-Key": credencial},
    )
    client.post(
        "/api/lite/operario/logout",
        json={"turno_fk": str(turno.id)},
        headers={"X-Device-Key": credencial},
    )

    r_login_2 = client.post(
        "/api/lite/operario/login",
        json={"legajo": operario_2.legajo, "turno_fk": str(turno.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r_login_2.status_code == 200
    asignacion_id = r_login_2.json()["asignacion_id"]

    fila = db.get(AsignacionTurno, uuid.UUID(asignacion_id))
    assert fila.operario_fk == operario_2.id
    assert fila.hora_salida is None


def test_logout_no_mezcla_tenants(client, db, tenant_a, tenant_b, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a)
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    turno, operario = _crear_turno_y_operario(db, tenant_a)

    client.post(
        "/api/lite/operario/login",
        json={"legajo": operario.legajo, "turno_fk": str(turno.id)},
        headers={"X-Device-Key": credencial},
    )

    # Un turno_fk de OTRO tenant no debería encontrar nada para cerrar
    # (la asignación real vive bajo tenant_a, filtrada por dispositivo.tenant_id).
    turno_b = Turno(tenant_id=tenant_b, nombre="Turno B", hora_inicio=time(6, 0), hora_fin=time(14, 0))
    db.add(turno_b)
    db.commit()
    db.refresh(turno_b)

    r = client.post(
        "/api/lite/operario/logout",
        json={"turno_fk": str(turno_b.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 404

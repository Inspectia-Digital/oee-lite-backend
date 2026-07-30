"""Fase K (auditoría QA #5): ingesta idempotente atómica bajo concurrencia
real. Dos requests con el mismo event_id disparados "al mismo tiempo" desde
threads distintos (cada uno abre su propia conexión a Postgres real, igual
que en producción) nunca deben terminar en 500, y nunca deben crear dos
filas para el mismo event_id."""
import uuid
import threading
from datetime import date, time as dtime

from sqlmodel import select

from app.models.domain import Estacion, Linea, LiteEventoProduccion, Planta
from tests.conftest import autenticar_como


def _preparar_estacion(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta Concurrencia")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea Concurrencia")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación Concurrencia", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def test_mismo_event_id_concurrente_no_duplica_ni_devuelve_500(client, db, tenant_a, gerente_a):
    estacion = _preparar_estacion(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    event_id = str(uuid.uuid4())
    payload = {"event_id": event_id, "id_estacion": str(estacion.id)}
    headers = {"X-Device-Key": credencial}

    resultados = []

    def _disparar():
        r = client.post("/api/lite/scans", json=payload, headers=headers)
        resultados.append(r.status_code)

    hilos = [threading.Thread(target=_disparar) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    # Ninguno de los dos terminó en 500 (antes: colisión de unique constraint
    # sin capturar). Ambos convergen a éxito (201 el que ganó la carrera, 200
    # idempotente el otro) -- nunca un error.
    assert all(codigo in (200, 201) for codigo in resultados), resultados

    filas = db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.event_id == uuid.UUID(event_id))).all()
    assert len(filas) == 1


def test_eventos_concurrentes_distintos_misma_estacion_no_generan_500(client, db, tenant_a, gerente_a):
    """Dos event_id DISTINTOS para la misma estación, disparados a la vez:
    el lock por estación los serializa (uno espera al otro) en vez de leer
    el mismo 'último evento' y calcular deltas inconsistentes."""
    estacion = _preparar_estacion(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    headers = {"X-Device-Key": credencial}

    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    resultados = []

    def _disparar(event_id):
        r = client.post("/api/lite/scans", json={"event_id": event_id, "id_estacion": str(estacion.id)}, headers=headers)
        resultados.append(r.status_code)

    hilos = [threading.Thread(target=_disparar, args=(eid,)) for eid in ids]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert all(codigo == 201 for codigo in resultados), resultados
    filas = db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id))).all()
    assert len(filas) == 2

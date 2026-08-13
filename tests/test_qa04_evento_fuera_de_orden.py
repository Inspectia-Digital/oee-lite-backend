"""QA-04 (auditoría QA): un evento con timestamp anterior al último
persistido para la misma estación (Edge/PLC que reenvía fuera de orden,
ej. tras un corte de conectividad con buffer local) daba delta_t_segundos
negativo. Eso violaba el CHECK delta_t_segundos >= 0 de la base
(migración 7af24f2546a7) recién al hacer commit -- y el manejo de
IntegrityError de scans.py asumía SIEMPRE que la falla era una colisión
de event_id, devolviendo un 409 "event_id ya usado con un payload
diferente" totalmente engañoso, con el evento perdido sin rastro claro.

Decisión del usuario: rechazar explícito con un código distinguible
(EVENTO_FUERA_DE_ORDEN), sin intentar reordenar/recomputar automático.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import Estacion, Linea, LiteEventoProduccion, Planta
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta QA-04")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea QA-04", tiempo_ideal_seg=5, tiempo_lento_seg=500, tiempo_alerta_seg=1000)
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación QA-04", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _postear(client, credencial, estacion_id, ts):
    return client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id), "timestamp": ts.isoformat()},
        headers={"X-Device-Key": credencial},
    )


def test_evento_con_timestamp_anterior_al_ultimo_devuelve_409_distinguible(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc) - timedelta(hours=2)

    r1 = _postear(client, credencial, estacion.id, ahora)
    assert r1.status_code == 201

    # Fuera de orden: 10 minutos ANTES del último evento persistido.
    r2 = _postear(client, credencial, estacion.id, ahora - timedelta(minutes=10))
    assert r2.status_code == 409
    assert "EVENTO_FUERA_DE_ORDEN" in r2.json()["detail"]
    # Nunca "event_id ya usado" -- son dos causas distintas, no deben
    # confundirse (ese era justo el bug).
    assert "event_id ya usado" not in r2.json()["detail"]


def test_evento_fuera_de_orden_no_se_persiste(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc) - timedelta(hours=2)

    _postear(client, credencial, estacion.id, ahora)
    _postear(client, credencial, estacion.id, ahora - timedelta(minutes=10))  # rechazado

    eventos = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id))
    ).all()
    assert len(eventos) == 1  # sólo el primero -- el fuera de orden nunca se guardó


def test_evento_en_orden_sigue_funcionando(client, db, tenant_a, gerente_a):
    """Regresión: el fix no debe rechazar el caso normal (timestamps
    estrictamente crecientes)."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc) - timedelta(hours=2)

    r1 = _postear(client, credencial, estacion.id, ahora)
    assert r1.status_code == 201
    r2 = _postear(client, credencial, estacion.id, ahora + timedelta(seconds=10))
    assert r2.status_code == 201

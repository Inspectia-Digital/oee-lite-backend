"""Fase CA (auditoría de robustez, batch 3): cada rechazo de la Terminal
lleva un código estructurado en el header `X-Error-Code` (app/core/errors.py),
sin tocar `detail` (contrato de texto libre que ya usan integraciones
externas, ver comentario en errors.py). Cubre una muestra representativa
de los 16 sitios (no los 16 -- varios comparten exactamente el mismo
código/patrón de rechazo ya cubierto en otras suites: test_rate_limit.py,
test_logout_operario.py)."""
import uuid
from datetime import datetime, timedelta, time, timezone

from sqlmodel import select

from app.models.domain import AsignacionTurno, Estacion, Linea, Operario, Planta, Turno
from tests.conftest import autenticar_como


def _crear_estacion_con_planta(db, tenant_id, nombre="Estación CA"):
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
    return estacion


def _emitir_api_key(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


# ---------- auth_m2m.py: 401 (credencial) ----------

def test_credencial_faltante_manda_codigo(client):
    estacion_id = str(uuid.uuid4())
    r = client.get(f"/api/lite/estaciones/{estacion_id}/validar")
    assert r.status_code == 401
    assert r.headers["x-error-code"] == "CREDENCIAL_FALTANTE"


def test_credencial_formato_invalido_manda_codigo(client):
    estacion_id = str(uuid.uuid4())
    r = client.get(
        f"/api/lite/estaciones/{estacion_id}/validar",
        headers={"X-Device-Key": "sin-punto-separador"},
    )
    assert r.status_code == 401
    assert r.headers["x-error-code"] == "CREDENCIAL_FORMATO_INVALIDO"


def test_credencial_inexistente_manda_codigo(client):
    estacion_id = str(uuid.uuid4())
    r = client.get(
        f"/api/lite/estaciones/{estacion_id}/validar",
        headers={"X-Device-Key": f"{uuid.uuid4()}.secretoquenoexiste"},
    )
    assert r.status_code == 401
    assert r.headers["x-error-code"] == "CREDENCIAL_INVALIDA"


def test_credencial_revocada_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Rev CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    key_id = credencial.split(".", 1)[0]

    from app.models.domain import ApiKeyDispositivo
    api_key = db.exec(select(ApiKeyDispositivo).where(ApiKeyDispositivo.key_id == key_id)).first()
    api_key.activo = False
    db.add(api_key)
    db.commit()

    r = client.get(
        f"/api/lite/estaciones/{estacion.id}/validar",
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 403
    assert r.headers["x-error-code"] == "CREDENCIAL_REVOCADA"


def test_credencial_expirada_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Exp CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    key_id = credencial.split(".", 1)[0]

    from app.models.domain import ApiKeyDispositivo
    api_key = db.exec(select(ApiKeyDispositivo).where(ApiKeyDispositivo.key_id == key_id)).first()
    api_key.expires_at = datetime.utcnow() - timedelta(days=1)
    db.add(api_key)
    db.commit()

    r = client.get(
        f"/api/lite/estaciones/{estacion.id}/validar",
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 403
    assert r.headers["x-error-code"] == "CREDENCIAL_EXPIRADA"


# ---------- scans.py: 403 estación no autorizada ----------

def test_estacion_no_autorizada_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Auth CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    otra_estacion_id = str(uuid.uuid4())

    r = client.get(
        f"/api/lite/estaciones/{otra_estacion_id}/validar",
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 403
    assert r.headers["x-error-code"] == "ESTACION_NO_AUTORIZADA"


# ---------- scans.py: 409 evento fuera de orden ----------

def test_evento_fuera_de_orden_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Orden CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    headers = {"X-Device-Key": credencial}

    # Sin `timestamp`: el backend usa datetime.now(UTC) del servidor (ver
    # _normalizar_timestamp_utc) -- evita el corrimiento de +3hs que sufre
    # un timestamp NAIVE (se interpreta en el timezone de planta, ART).
    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers=headers,
    )
    assert r1.status_code == 201

    # Explícitamente con offset UTC (no naive) para no repetir el mismo
    # problema -- un minuto antes de "ahora" en UTC real.
    anterior = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    r2 = client.post(
        "/api/lite/scans",
        json={
            "event_id": str(uuid.uuid4()),
            "id_estacion": str(estacion.id),
            "timestamp": anterior,
        },
        headers=headers,
    )
    assert r2.status_code == 409
    assert r2.headers["x-error-code"] == "EVENTO_FUERA_DE_ORDEN"


def test_evento_id_conflicto_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Conflicto CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    headers = {"X-Device-Key": credencial}
    event_id = str(uuid.uuid4())

    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": event_id, "id_estacion": str(estacion.id), "codigo_pieza": "A"},
        headers=headers,
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": event_id, "id_estacion": str(estacion.id), "codigo_pieza": "B"},
        headers=headers,
    )
    assert r2.status_code == 409
    assert r2.headers["x-error-code"] == "EVENTO_ID_CONFLICTO"


# ---------- scans.py: 400 cantidad rechazada inválida ----------

def test_cantidad_rechazada_invalida_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Rechazo CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/scans",
        json={
            "event_id": str(uuid.uuid4()),
            "id_estacion": str(estacion.id),
            "unidades_procesadas": 1,
            "unidades_rechazadas": 5,
        },
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 400
    assert r.headers["x-error-code"] == "CANTIDAD_RECHAZADA_INVALIDA"


# ---------- scans.py: login/logout de operario ----------

def test_operario_no_encontrado_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Login CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    turno = Turno(tenant_id=tenant_a, nombre="Turno CA", hora_inicio=time(6, 0), hora_fin=time(14, 0))
    db.add(turno)
    db.commit()
    db.refresh(turno)

    r = client.post(
        "/api/lite/operario/login",
        json={"legajo": "NO-EXISTE", "turno_fk": str(turno.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 404
    assert r.headers["x-error-code"] == "OPERARIO_NO_ENCONTRADO"


def test_turno_no_encontrado_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Turno CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)
    operario = Operario(tenant_id=tenant_a, legajo=f"LEG-{uuid.uuid4().hex[:6]}", nombre_completo="Operario CA")
    db.add(operario)
    db.commit()
    db.refresh(operario)

    r = client.post(
        "/api/lite/operario/login",
        json={"legajo": operario.legajo, "turno_fk": str(uuid.uuid4())},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 404
    assert r.headers["x-error-code"] == "TURNO_NO_ENCONTRADO"


def test_sesion_no_encontrada_manda_codigo(client, db, tenant_a, gerente_a):
    estacion = _crear_estacion_con_planta(db, tenant_a, "Logout CA")
    credencial = _emitir_api_key(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/operario/logout",
        json={"turno_fk": str(uuid.uuid4())},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 404
    assert r.headers["x-error-code"] == "SESION_NO_ENCONTRADA"


# ---------- rate_limit.py: 429 ----------

def test_rate_limit_manda_codigo(client):
    estacion_id = str(uuid.uuid4())
    ip_falsa = f"203.0.113.{uuid.uuid4().int % 250 + 1}"
    headers = {"X-Device-Key": "keyid.secretofalso", "X-Forwarded-For": ip_falsa}

    r = None
    for _ in range(32):
        r = client.get(f"/api/lite/estaciones/{estacion_id}/validar", headers=headers)
        if r.status_code == 429:
            break
    assert r.status_code == 429
    assert r.headers["x-error-code"] == "LIMITE_DE_INTENTOS"

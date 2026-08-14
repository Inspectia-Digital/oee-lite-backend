"""Fase BY (auditoría de robustez, batch 3): rate limiting basado en
Postgres (app/core/rate_limit.py). Cada test usa una clave/IP única
(uuid) para no interferir entre corridas -- la tabla rate_limit_bucket
es Postgres real y persistente, no una transacción que se revierte
sola (mismo criterio que el resto de conftest.py: aislar por ID único,
no por rollback)."""
import uuid

from fastapi import HTTPException

from app.core.rate_limit import verificar_limite
from tests.conftest import _id_unico


# ---------- unidad: verificar_limite ----------

def test_verificar_limite_permite_hasta_el_maximo(db):
    clave = _id_unico("rl_unit")
    for _ in range(5):
        verificar_limite(db, clave, max_intentos=5, ventana_segundos=60)
    # El 6to, dentro de la misma ventana, debe fallar.
    try:
        verificar_limite(db, clave, max_intentos=5, ventana_segundos=60)
        assert False, "debería haber lanzado 429"
    except HTTPException as e:
        assert e.status_code == 429


def test_verificar_limite_ventana_vencida_resetea_el_contador(db):
    from datetime import datetime, timedelta
    from sqlalchemy import text

    clave = _id_unico("rl_ventana")
    for _ in range(5):
        verificar_limite(db, clave, max_intentos=5, ventana_segundos=60)

    # Simula que pasó la ventana: mueve ventana_inicio al pasado.
    db.execute(
        text("UPDATE rate_limit_bucket SET ventana_inicio = :hace WHERE clave = :clave"),
        {"hace": datetime.utcnow() - timedelta(seconds=120), "clave": clave},
    )
    db.commit()

    # No debería tirar -- la ventana vencida resetea a 1.
    verificar_limite(db, clave, max_intentos=5, ventana_segundos=60)


def test_verificar_limite_claves_distintas_no_se_pisan(db):
    clave_a = _id_unico("rl_a")
    clave_b = _id_unico("rl_b")
    for _ in range(5):
        verificar_limite(db, clave_a, max_intentos=5, ventana_segundos=60)
    # clave_b sigue en cero, no debería fallar.
    verificar_limite(db, clave_b, max_intentos=5, ventana_segundos=60)


# ---------- integración: autenticar_dispositivo (m2m_auth) ----------

def test_autenticar_dispositivo_limita_intentos_de_credencial_invalida(client):
    ip_falsa = f"203.0.113.{uuid.uuid4().int % 250 + 1}"  # rango de test RFC 5737, única por corrida
    estacion_id = str(uuid.uuid4())
    headers = {"X-Device-Key": "keyid.secretofalso", "X-Forwarded-For": ip_falsa}

    respuestas = []
    for _ in range(32):  # max_intentos=30 en autenticar_dispositivo
        r = client.get(f"/api/lite/estaciones/{estacion_id}/validar", headers=headers)
        respuestas.append(r.status_code)

    # Los primeros son 401 (credencial inválida, no encontrada); en algún
    # punto dentro de la ventana el limiter corta con 429.
    assert 401 in respuestas
    assert 429 in respuestas
    # El 429 debe aparecer DESPUÉS de haber agotado el cupo, no antes.
    assert respuestas.index(429) >= 30

"""Fase BY (auditoría de robustez, batch 3): rate limiting basado en
Postgres, no en memoria.

El backend corre en Cloud Run (deploy-dev.yml: `--min-instances 0
--max-instances 5`) -- cada instancia es un proceso Python separado que
puede escalar a cero y volver a arrancar en frío. Un limiter in-memory
(un dict con contadores) sólo protegería la fracción de tráfico que le
toque a ESA instancia puntual, y perdería todo el estado en cada
cold-start -- una protección que en la práctica no protege nada estable.
Postgres ya es compartido por todas las instancias, así que un contador
ahí es la única forma correcta de limitar sin agregar infraestructura
nueva (Redis).

Ventana fija (no deslizante) -- más simple, alcanza para frenar abuso
grosero (credential-stuffing, loops con bug); no pretende ser precisión
de milisegundo. El UPSERT es atómico (una sola sentencia con
ON CONFLICT ... RETURNING), así que dos instancias de Cloud Run
incrementando la misma clave al mismo tiempo no pisan el conteo una de
la otra.
"""
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlmodel import Session

from app.core.errors import ErrorCode


def verificar_limite(db: Session, clave: str, *, max_intentos: int, ventana_segundos: int) -> None:
    """Incrementa el contador de `clave` y aborta con 429 si superó
    `max_intentos` dentro de los últimos `ventana_segundos`. Se llama
    ANTES de hacer el trabajo real (verificar credencial, crear la key,
    etc.) -- cuenta también los intentos que van a fallar, que es
    justamente lo que hay que frenar."""
    ahora = datetime.utcnow()
    ventana_minima = ahora - timedelta(seconds=ventana_segundos)

    fila = db.execute(
        text(
            """
            INSERT INTO rate_limit_bucket (clave, ventana_inicio, intentos)
            VALUES (:clave, :ahora, 1)
            ON CONFLICT (clave) DO UPDATE SET
                intentos = CASE
                    WHEN rate_limit_bucket.ventana_inicio < :ventana_minima THEN 1
                    ELSE rate_limit_bucket.intentos + 1
                END,
                ventana_inicio = CASE
                    WHEN rate_limit_bucket.ventana_inicio < :ventana_minima THEN :ahora
                    ELSE rate_limit_bucket.ventana_inicio
                END
            RETURNING intentos
            """
        ),
        {"clave": clave, "ahora": ahora, "ventana_minima": ventana_minima},
    ).first()
    db.commit()

    intentos = fila[0] if fila else 1
    if intentos > max_intentos:
        # Fase CA: código estructurado en el header, `detail` sin cambios
        # (ver app/core/errors.py -- mismo criterio en todo el módulo).
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos en poco tiempo. Esperá un momento y volvé a intentar.",
            headers={"X-Error-Code": ErrorCode.LIMITE_DE_INTENTOS.value},
        )


def clave_por_ip(request, prefijo: str) -> str:
    """IP del cliente real detrás del load balancer de Cloud Run --
    X-Forwarded-For trae la cadena completa de proxies, el primer valor
    es el cliente original."""
    xff = request.headers.get("x-forwarded-for")
    ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "desconocida")
    return f"{prefijo}:{ip}"

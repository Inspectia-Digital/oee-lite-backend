"""Observabilidad mínima (Fase K, auditoría QA #17).

Alcance deliberadamente acotado (criterio Green Mills primero): un
request-id correlacionable end-to-end (cliente -> logs del backend ->
respuesta) y un log de acceso estructurado por request (método, path,
status, duración). Métricas, tracing distribuido, SLOs y alertas quedan
pospuestos -- son inversión de escala/observabilidad multi-tenant, no
algo que Green Mills necesite para salir a producción.
"""
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_access_logger = logging.getLogger("app.access")


def obtener_request_id_actual() -> str:
    """Para que cualquier log de negocio pueda correlacionarse con el
    request en curso sin tener que pasarlo a mano por cada función."""
    return _request_id_ctx.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Genera (o respeta, si ya viene de un proxy/gateway) un X-Request-Id
    por request, lo devuelve en la respuesta, y loguea una línea de acceso
    estructurada por cada request procesado."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        token = _request_id_ctx.set(request_id)
        inicio = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duracion_ms = (time.monotonic() - inicio) * 1000
            _access_logger.error(
                "method=%s path=%s status=500 duration_ms=%.1f request_id=%s (excepción no manejada)",
                request.method, request.url.path, duracion_ms, request_id,
            )
            raise
        finally:
            _request_id_ctx.reset(token)

        duracion_ms = (time.monotonic() - inicio) * 1000
        response.headers["X-Request-Id"] = request_id
        _access_logger.info(
            "method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
            request.method, request.url.path, response.status_code, duracion_ms, request_id,
        )
        return response

"""Autenticación M2M real para hardware fijo (Fase D.4a).

Formato de credencial: "key_id.secret", enviada en el header X-Device-Key
(separado del Authorization: Bearer humano a propósito, para no mezclar
JWT de usuario con credencial de dispositivo).

Códigos de error según HANDOFF_STG_PRODUCTION_GRADE.md:
- credencial ausente o con formato inválido, o key_id/secret que no
  matchean: 401 (no autenticado).
- key encontrada pero revocada, expirada, o estación no autorizada, o
  tenant en SUSPENSION_TOTAL: 403 (autenticado pero no autorizado).
Nunca 404: no confirmar/negar la existencia de recursos a un llamador
no autenticado.
"""
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, Header, Request, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.auth import verificar_no_suspension_total
from app.core.database import get_session
from app.core.errors import ErrorCode, error_terminal
from app.core.rate_limit import clave_por_ip, verificar_limite
from app.models.domain import ApiKeyDispositivo, Tenant


class ContextoDispositivo(BaseModel):
    tenant_id: str
    estacion_id: str
    api_key_id: str


# Fase CB (auditoría de robustez, batch 3): ventana de actualización de
# `ultimo_uso_at` -- una terminal activa autentica un scan POR PIEZA
# (potencialmente varias por minuto). Escribir la fila en cada auth
# exitosa multiplicaría la carga de escritura de este hot path sin
# necesidad real: para "hace cuánto se usó por última vez" alcanza con
# resolución de minutos, no de milisegundos.
VENTANA_ULTIMO_USO = timedelta(minutes=5)


def _actualizar_ultimo_uso(db: Session, api_key: ApiKeyDispositivo) -> None:
    ahora = datetime.utcnow()
    if api_key.ultimo_uso_at and (ahora - api_key.ultimo_uso_at) < VENTANA_ULTIMO_USO:
        return
    api_key.ultimo_uso_at = ahora
    db.add(api_key)
    db.commit()


def autenticar_dispositivo(
    request: Request,
    x_device_key: Optional[str] = Header(None, alias="X-Device-Key", description="Formato: key_id.secret"),
    db: Session = Depends(get_session),
) -> ContextoDispositivo:
    # Fase BY: único punto de entrada de TODO el tráfico M2M (scans,
    # login de operario, etc.) -- el lugar de más impacto para frenar
    # intentos de credencial ADIVINADA por IP. Importante: el límite se
    # cuenta SÓLO en los rechazos 401 de abajo (credencial ausente/mal
    # formada/no encontrada/secret que no matchea) -- esos son los únicos
    # que corresponden a alguien probando una credencial. Una key
    # revocada/expirada o un tenant suspendido (403 más abajo) ya
    # demostró conocer el secret real -- es tráfico de un dispositivo
    # legítimo reintentando en operación normal (posiblemente a alta
    # frecuencia, un scan por pieza), no un ataque; contar eso habría
    # rate-limiteado el escaneo normal de una línea activa.
    # Fase CA: cada rechazo lleva además un ErrorCode estructurado (header
    # X-Error-Code, ver app/core/errors.py) -- `detail` no cambia.
    def _rechazar(mensaje: str, code: ErrorCode):
        verificar_limite(db, clave_por_ip(request, "m2m_auth"), max_intentos=30, ventana_segundos=60)
        raise error_terminal(status.HTTP_401_UNAUTHORIZED, code, mensaje)

    if not x_device_key:
        _rechazar("Falta la credencial del dispositivo (X-Device-Key).", ErrorCode.CREDENCIAL_FALTANTE)

    if "." not in x_device_key:
        _rechazar("Formato de credencial inválido.", ErrorCode.CREDENCIAL_FORMATO_INVALIDO)

    key_id, _, secret = x_device_key.partition(".")
    if not key_id or not secret:
        _rechazar("Formato de credencial inválido.", ErrorCode.CREDENCIAL_FORMATO_INVALIDO)

    api_key = db.exec(select(ApiKeyDispositivo).where(ApiKeyDispositivo.key_id == key_id)).first()
    if not api_key:
        _rechazar("Credencial inválida.", ErrorCode.CREDENCIAL_INVALIDA)

    if not bcrypt.checkpw(secret.encode("utf-8"), api_key.secret_hash.encode("utf-8")):
        _rechazar("Credencial inválida.", ErrorCode.CREDENCIAL_INVALIDA)

    if not api_key.activo:
        raise error_terminal(status.HTTP_403_FORBIDDEN, ErrorCode.CREDENCIAL_REVOCADA, "Credencial revocada.")

    if api_key.expires_at < datetime.utcnow():
        raise error_terminal(status.HTTP_403_FORBIDDEN, ErrorCode.CREDENCIAL_EXPIRADA, "Credencial expirada.")

    tenant = db.get(Tenant, api_key.tenant_id)
    verificar_no_suspension_total(tenant)

    _actualizar_ultimo_uso(db, api_key)

    return ContextoDispositivo(
        tenant_id=api_key.tenant_id,
        estacion_id=str(api_key.estacion_id),
        api_key_id=str(api_key.id),
    )

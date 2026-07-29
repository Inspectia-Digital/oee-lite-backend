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
from datetime import datetime
from typing import Optional

import bcrypt
from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.auth import verificar_no_suspension_total
from app.core.database import get_session
from app.models.domain import ApiKeyDispositivo, Tenant


class ContextoDispositivo(BaseModel):
    tenant_id: str
    estacion_id: str
    api_key_id: str


def autenticar_dispositivo(
    x_device_key: Optional[str] = Header(None, alias="X-Device-Key", description="Formato: key_id.secret"),
    db: Session = Depends(get_session),
) -> ContextoDispositivo:
    if not x_device_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Falta la credencial del dispositivo (X-Device-Key).")

    if "." not in x_device_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Formato de credencial inválido.")

    key_id, _, secret = x_device_key.partition(".")
    if not key_id or not secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Formato de credencial inválido.")

    api_key = db.exec(select(ApiKeyDispositivo).where(ApiKeyDispositivo.key_id == key_id)).first()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credencial inválida.")

    if not bcrypt.checkpw(secret.encode("utf-8"), api_key.secret_hash.encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credencial inválida.")

    if not api_key.activo:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Credencial revocada.")

    if api_key.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Credencial expirada.")

    tenant = db.get(Tenant, api_key.tenant_id)
    verificar_no_suspension_total(tenant)

    return ContextoDispositivo(
        tenant_id=api_key.tenant_id,
        estacion_id=str(api_key.estacion_id),
        api_key_id=str(api_key.id),
    )

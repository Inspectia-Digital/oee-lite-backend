"""CRUD de credenciales M2M para hardware fijo (Fase D.1).

Formato entregado al cliente: "key_id.secret". Sólo se devuelve el secret
completo en la respuesta de creación; después es irrecuperable (sólo se
persiste su hash bcrypt). Emisión y revocación exclusivas de
Gerencia/SuperAdmin. Máximo dos keys activas por estación.
"""
import secrets
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.core.auth import TenantContext, obtener_contexto_tenant_humano
from app.core.database import get_session
from app.core.rate_limit import verificar_limite
from app.core.rbac import requerir_gerencia_o_superadmin
from app.models.domain import ApiKeyDispositivo, Estacion, UsuarioSaaS

router = APIRouter(prefix="/config/api-keys", tags=["Dispositivos M2M"])

MAX_KEYS_ACTIVAS_POR_ESTACION = 2
EXPIRACION_DEFAULT_DIAS = 365


class ApiKeyCreate(BaseModel):
    estacion_id: uuid.UUID


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    key_id: str
    estacion_id: uuid.UUID
    activo: bool
    expires_at: datetime
    created_at: datetime
    revoked_at: Optional[datetime] = None
    # Fase CB (auditoría de robustez, batch 3): trazabilidad de historial.
    creado_por_id: Optional[uuid.UUID] = None
    creado_por_nombre: Optional[str] = None
    ultimo_uso_at: Optional[datetime] = None


class ApiKeyCreadaResponse(ApiKeyResponse):
    secret: str
    credencial_completa: str


def _generar_key_id() -> str:
    return secrets.token_hex(8)


def _generar_secret() -> str:
    return secrets.token_urlsafe(32)


def _hashear_secret(secret: str) -> str:
    return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _nombre_usuario(usuario: UsuarioSaaS) -> str:
    completo = " ".join(p for p in [usuario.nombre, usuario.apellido] if p).strip()
    return completo or usuario.email or "Usuario sin nombre"


@router.post("/", response_model=ApiKeyCreadaResponse, status_code=status.HTTP_201_CREATED)
def emitir_api_key(
    payload: ApiKeyCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    usuario: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    # Fase BY: ya está gateado por rol Gerencia/SuperAdmin y por el tope
    # de negocio MAX_KEYS_ACTIVAS_POR_ESTACION -- este límite es defensa
    # en profundidad extra (ej. un bug de frontend en loop), no la
    # protección principal.
    verificar_limite(db, f"api_key_create:{usuario.id}", max_intentos=10, ventana_segundos=60)

    estacion = db.exec(
        select(Estacion).where(Estacion.id == payload.estacion_id, Estacion.tenant_id == context.tenant_id)
    ).first()
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada o de otro tenant.")

    activas = db.exec(
        select(func.count(ApiKeyDispositivo.id)).where(
            ApiKeyDispositivo.estacion_id == payload.estacion_id,
            ApiKeyDispositivo.tenant_id == context.tenant_id,
            ApiKeyDispositivo.activo == True,  # noqa: E712
        )
    ).one()
    if activas >= MAX_KEYS_ACTIVAS_POR_ESTACION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"La estación ya tiene {MAX_KEYS_ACTIVAS_POR_ESTACION} API keys activas (máximo permitido). Revocá una antes de crear otra.",
        )

    key_id = _generar_key_id()
    secret = _generar_secret()

    nueva_key = ApiKeyDispositivo(
        tenant_id=context.tenant_id,
        key_id=key_id,
        secret_hash=_hashear_secret(secret),
        estacion_id=payload.estacion_id,
        activo=True,
        expires_at=datetime.utcnow() + timedelta(days=EXPIRACION_DEFAULT_DIAS),
        # Fase CB: quién la emitió -- ya autorizado por requerir_gerencia_o_superadmin arriba.
        creado_por_id=usuario.id,
    )
    db.add(nueva_key)
    db.commit()
    db.refresh(nueva_key)

    return ApiKeyCreadaResponse(
        id=nueva_key.id,
        key_id=nueva_key.key_id,
        estacion_id=nueva_key.estacion_id,
        activo=nueva_key.activo,
        expires_at=nueva_key.expires_at,
        created_at=nueva_key.created_at,
        revoked_at=nueva_key.revoked_at,
        creado_por_id=nueva_key.creado_por_id,
        creado_por_nombre=_nombre_usuario(usuario),
        ultimo_uso_at=nueva_key.ultimo_uso_at,
        secret=secret,
        credencial_completa=f"{key_id}.{secret}",
    )


@router.get("/", response_model=List[ApiKeyResponse])
def listar_api_keys(
    estacion_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    """Fase CB: incluye activas Y revocadas -- historial completo, no sólo
    lo vigente (el filtro "sólo activas" vive en el frontend, no acá).
    Devuelve además `creado_por_nombre` resuelto con un solo query batch
    (no N+1) para las keys que tienen `creado_por_id` (las emitidas antes
    de esta fase no lo tienen, quedan en None)."""
    query = select(ApiKeyDispositivo).where(ApiKeyDispositivo.tenant_id == context.tenant_id)
    if estacion_id:
        query = query.where(ApiKeyDispositivo.estacion_id == estacion_id)
    keys = db.exec(query.order_by(ApiKeyDispositivo.created_at.desc())).all()

    creador_ids = {k.creado_por_id for k in keys if k.creado_por_id}
    nombres_por_id = {}
    if creador_ids:
        creadores = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.id.in_(creador_ids))).all()
        nombres_por_id = {u.id: _nombre_usuario(u) for u in creadores}

    return [
        ApiKeyResponse(
            id=k.id,
            key_id=k.key_id,
            estacion_id=k.estacion_id,
            activo=k.activo,
            expires_at=k.expires_at,
            created_at=k.created_at,
            revoked_at=k.revoked_at,
            creado_por_id=k.creado_por_id,
            creado_por_nombre=nombres_por_id.get(k.creado_por_id),
            ultimo_uso_at=k.ultimo_uso_at,
        )
        for k in keys
    ]


@router.post("/{api_key_id}/revocar", response_model=ApiKeyResponse)
def revocar_api_key(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    api_key = db.exec(
        select(ApiKeyDispositivo).where(
            ApiKeyDispositivo.id == api_key_id, ApiKeyDispositivo.tenant_id == context.tenant_id
        )
    ).first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key no encontrada.")

    api_key.activo = False
    api_key.revoked_at = datetime.utcnow()
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key

import uuid
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel, field_validator

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext, get_usuario_actual
from app.core.rbac import requerir_gerencia_produccion_o_superadmin
from app.models.domain import Planta, UsuarioSaaS, RolUsuario

router = APIRouter(prefix="/accesos", tags=["Organización Física"])


def _validar_timezone_iana(v: str) -> str:
    """Fase DP (auditoría de backend, P1-03): antes `timezone` se
    persistía tal cual, sin validar contra la base IANA -- un typo
    ("Norteamerica/Mexico") se guardaba sin error y rompía en silencio
    cualquier cálculo de fecha/turno que dependiera de esa planta
    (login/logout de operario, recómputo, dashboard)."""
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValueError(f"'{v}' no es un timezone IANA válido (ej: 'America/Buenos_Aires').")
    return v


class PlantaCreate(BaseModel):
    nombre: str
    ubicacion: str = "N/A"
    timezone: str = "America/Buenos_Aires"

    @field_validator("timezone")
    @classmethod
    def _timezone_valido(cls, v: str) -> str:
        return _validar_timezone_iana(v)

class PlantaUpdate(BaseModel):
    nombre: Optional[str] = None
    ubicacion: Optional[str] = None
    timezone: Optional[str] = None
    activo: Optional[bool] = None

    @field_validator("timezone")
    @classmethod
    def _timezone_valido(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validar_timezone_iana(v)


def _serializar(p: Planta) -> dict:
    return {
        "id": str(p.id), "nombre": p.nombre, "ubicacion": p.ubicacion,
        "timezone": p.timezone, "activo": p.activo,
    }


@router.get("/mi-empresa/sub-tenants")
def listar_plantas(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """Devuelve las plantas (sub-tenants) pertenecientes a la empresa (tenant) logueada.
    GET normal excluye inactivas; incluir_inactivos=true sólo para Gerencia/SuperAdmin."""
    if incluir_inactivos and usuario_actual.rol not in (RolUsuario.SUPERADMIN, RolUsuario.GERENCIA):
        raise HTTPException(status_code=403, detail="Sólo Gerencia o SuperAdmin pueden ver plantas inactivas.")

    query = select(Planta).where(Planta.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(Planta.activo == True)  # noqa: E712
    plantas = db.exec(query).all()
    return [_serializar(p) for p in plantas]


@router.get("/mi-empresa/sub-tenants/{planta_id}")
def obtener_planta(
    planta_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    planta = db.exec(select(Planta).where(Planta.id == planta_id, Planta.tenant_id == context.tenant_id)).first()
    if not planta:
        raise HTTPException(status_code=404, detail="Planta no encontrada.")
    return _serializar(planta)


@router.post("/mi-empresa/sub-tenants", status_code=status.HTTP_201_CREATED)
def crear_planta(
    payload: PlantaCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_produccion_o_superadmin),
):
    """Crea una nueva planta física aislada bajo el Tenant actual.
    Antes cualquier usuario autenticado podía crear una planta (sin RBAC); corregido.

    P1-A (PRD Go-Live Green Mills): Producción también puede crear/editar
    plantas (incluido cambiar timezone) -- mismo criterio ya establecido
    en Fase DC para gestión de supervisores (requerir_gerencia_produccion_
    o_superadmin, app/core/rbac.py)."""
    nueva_planta = Planta(
        tenant_id=context.tenant_id,
        nombre=payload.nombre,
        ubicacion=payload.ubicacion,
        timezone=payload.timezone,
    )
    db.add(nueva_planta)
    db.commit()
    db.refresh(nueva_planta)
    return _serializar(nueva_planta)


@router.patch("/mi-empresa/sub-tenants/{planta_id}")
def actualizar_planta(
    planta_id: uuid.UUID,
    payload: PlantaUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_produccion_o_superadmin),
):
    planta = db.exec(select(Planta).where(Planta.id == planta_id, Planta.tenant_id == context.tenant_id)).first()
    if not planta:
        raise HTTPException(status_code=404, detail="Planta no encontrada.")

    datos = payload.model_dump(exclude_unset=True)
    for key, value in datos.items():
        setattr(planta, key, value)

    db.add(planta)
    db.commit()
    db.refresh(planta)
    return _serializar(planta)


@router.delete("/mi-empresa/sub-tenants/{planta_id}")
def desactivar_planta(
    planta_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_produccion_o_superadmin),
):
    """Baja lógica (activo=False). Nunca hard-delete: hay líneas/estaciones
    y trazabilidad histórica que dependen de esta planta."""
    planta = db.exec(select(Planta).where(Planta.id == planta_id, Planta.tenant_id == context.tenant_id)).first()
    if not planta:
        raise HTTPException(status_code=404, detail="Planta no encontrada.")

    planta.activo = False
    db.add(planta)
    db.commit()
    return {"mensaje": "Planta desactivada."}

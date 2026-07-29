import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext, get_usuario_actual
from app.core.rbac import requerir_gerencia_o_superadmin
from app.models.domain import Planta, UsuarioSaaS, RolUsuario

router = APIRouter(prefix="/accesos", tags=["Organización Física"])

class PlantaCreate(BaseModel):
    nombre: str
    ubicacion: str = "N/A"
    timezone: str = "America/Buenos_Aires"

class PlantaUpdate(BaseModel):
    nombre: Optional[str] = None
    ubicacion: Optional[str] = None
    timezone: Optional[str] = None
    activo: Optional[bool] = None


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
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    """Crea una nueva planta física aislada bajo el Tenant actual.
    Antes cualquier usuario autenticado podía crear una planta (sin RBAC); corregido."""
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
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
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
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
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

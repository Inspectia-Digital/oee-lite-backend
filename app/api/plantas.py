from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from pydantic import BaseModel

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant, TenantContext
from app.models.domain import Planta

router = APIRouter(prefix="/accesos", tags=["Organización Física"])

class PlantaCreate(BaseModel):
    nombre: str
    ubicacion: str = "N/A"

@router.get("/mi-empresa/sub-tenants")
def listar_plantas(
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    """Devuelve las plantas (sub-tenants) pertenecientes a la empresa (tenant) logueada."""
    plantas = db.exec(
        select(Planta).where(Planta.tenant_id == context.tenant_id)
    ).all()
    
    # El Frontend suele esperar este mapeo de campos
    return [{"id": str(p.id), "nombre": p.nombre, "ubicacion": p.ubicacion} for p in plantas]


@router.post("/mi-empresa/sub-tenants")
def crear_planta(
    payload: PlantaCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    """Crea una nueva planta física aislada bajo el Tenant actual."""
    nueva_planta = Planta(
        tenant_id=context.tenant_id,
        nombre=payload.nombre,
        ubicacion=payload.ubicacion
    )
    db.add(nueva_planta)
    db.commit()
    db.refresh(nueva_planta)
    
    return {"id": str(nueva_planta.id), "nombre": nueva_planta.nombre, "ubicacion": nueva_planta.ubicacion}
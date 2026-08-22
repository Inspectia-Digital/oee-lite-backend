"""Fase FA.2 (PRD Demo/Partners/Marketplace/Soporte/Planes): Tenant tipo
Partner/Canal/Consultor.

- Material comercial: CRUD SuperAdmin (global, no por partner) +
  lectura para cualquier usuario de un tenant categoria=partner.
- Alta de partner: NO se duplica acá -- reusa POST /accesos/superadmin/
  tenants (admin.py), que ya acepta `categoria` en el payload (ver
  TenantCreate). Este router es sólo materiales_partner.
"""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.auth import TenantContext, obtener_contexto_tenant_humano
from app.core.database import get_session
from app.core.rbac import requerir_superadmin
from app.models.domain import MaterialPartner, Tenant, UsuarioSaaS

router = APIRouter(tags=["Partners (Material Comercial)"])


class MaterialPartnerCreate(BaseModel):
    titulo: str
    descripcion: Optional[str] = None
    url_archivo: Optional[str] = None
    categoria: str = "otro"
    visible: bool = True


class MaterialPartnerUpdate(BaseModel):
    titulo: Optional[str] = None
    descripcion: Optional[str] = None
    url_archivo: Optional[str] = None
    categoria: Optional[str] = None
    visible: Optional[bool] = None


# ==========================================
# SUPERADMIN: catálogo global (misma tabla que consume el partner)
# ==========================================
@router.get("/admin/materiales-partner", response_model=List[MaterialPartner])
def listar_materiales_admin(
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    return db.exec(select(MaterialPartner).order_by(MaterialPartner.creado_at.desc())).all()


@router.post("/admin/materiales-partner", response_model=MaterialPartner, status_code=status.HTTP_201_CREATED)
def crear_material(
    payload: MaterialPartnerCreate,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    material = MaterialPartner(**payload.model_dump())
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


@router.put("/admin/materiales-partner/{material_id}", response_model=MaterialPartner)
def actualizar_material(
    material_id: uuid.UUID,
    payload: MaterialPartnerUpdate,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    material = db.get(MaterialPartner, material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(material, key, value)
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


@router.delete("/admin/materiales-partner/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_material(
    material_id: uuid.UUID,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    material = db.get(MaterialPartner, material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")
    db.delete(material)
    db.commit()


# ==========================================
# PARTNER: sólo lectura de lo visible, sólo si su tenant es categoria=partner
# ==========================================
@router.get("/mi-empresa/materiales-partner", response_model=List[MaterialPartner])
def listar_materiales_partner(
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    tenant = db.get(Tenant, context.tenant_id)
    if not tenant or tenant.categoria != "partner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sección exclusiva de empresas Partner.",
        )
    return db.exec(
        select(MaterialPartner).where(MaterialPartner.visible == True).order_by(MaterialPartner.creado_at.desc())  # noqa: E712
    ).all()

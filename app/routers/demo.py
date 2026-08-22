"""Fase FA (PRD Demo/Partners/Marketplace/Soporte/Planes): Ambiente Demo
autoservicio para el equipo comercial. Ver app/core/demo_simulador.py
para la lógica de creación/simulación/limpieza -- este router es sólo
el CRUD/control HTTP, `requerir_superadmin` en todo: crear/gestionar
demos es una acción de plataforma, no algo que un tenant cliente pueda
disparar."""
import logging
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.demo_industrias import INDUSTRIAS_VALIDAS
from app.core.demo_simulador import (
    crear_estructura_demo, eliminar_demo_manual, reiniciar_datos_generados,
)
from app.core.rbac import requerir_superadmin
from app.models.domain import Tenant, UsuarioSaaS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/demo", tags=["Ambiente Demo (SuperAdmin)"])


class DemoCreate(BaseModel):
    nombre: str
    industria: str
    tamano: Literal["chica", "mediana"] = "chica"


class DemoOut(BaseModel):
    id: str
    nombre: str
    industria_demo: Optional[str]
    demo_simulando_desde: Optional[datetime]
    demo_expira_at: Optional[datetime]
    demo_velocidad: str


class SimularIniciar(BaseModel):
    velocidad: Literal["lenta", "normal", "rapida"] = "normal"


def _obtener_demo_o_404(db: Session, tenant_id: str) -> Tenant:
    tenant = db.exec(select(Tenant).where(Tenant.id == tenant_id, Tenant.es_demo == True)).first()  # noqa: E712
    if not tenant:
        raise HTTPException(status_code=404, detail="Demo no encontrada.")
    return tenant


@router.post("/crear", response_model=DemoOut, status_code=status.HTTP_201_CREATED)
def crear_demo(
    payload: DemoCreate,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    if payload.industria not in INDUSTRIAS_VALIDAS:
        raise HTTPException(
            status_code=422,
            detail=f"Industria inválida. Válidas: {', '.join(INDUSTRIAS_VALIDAS)}.",
        )
    if not payload.nombre.strip():
        raise HTTPException(status_code=422, detail="El nombre es obligatorio.")
    tenant = crear_estructura_demo(db, payload.nombre.strip(), payload.industria, payload.tamano)
    return tenant


@router.get("/", response_model=List[DemoOut])
def listar_demos(
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    return db.exec(select(Tenant).where(Tenant.es_demo == True).order_by(Tenant.created_at.desc())).all()  # noqa: E712


@router.post("/{tenant_id}/simular/iniciar", response_model=DemoOut)
def iniciar_simulacion(
    tenant_id: str,
    payload: SimularIniciar,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    tenant = _obtener_demo_o_404(db, tenant_id)
    tenant.demo_simulando_desde = datetime.utcnow()
    tenant.demo_velocidad = payload.velocidad
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.post("/{tenant_id}/simular/detener", response_model=DemoOut)
def detener_simulacion(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    tenant = _obtener_demo_o_404(db, tenant_id)
    tenant.demo_simulando_desde = None
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.post("/{tenant_id}/reiniciar", status_code=status.HTTP_204_NO_CONTENT)
def reiniciar_demo(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    _obtener_demo_o_404(db, tenant_id)
    reiniciar_datos_generados(db, tenant_id)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_demo(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    _obtener_demo_o_404(db, tenant_id)
    eliminar_demo_manual(tenant_id)

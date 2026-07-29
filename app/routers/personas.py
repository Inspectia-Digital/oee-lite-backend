"""CRUD de Operario y Supervisor (Fase "API/CRUD completo").

Antes no existía ningún router para estas entidades pese a que el
modelo ya estaba desde el inicio del proyecto. Mismo patrón que el
resto de los maestros: baja lógica (activo), incluir_inactivos sólo
Gerencia/SuperAdmin, legajo único por tenant entre filas activas (el
índice único parcial ya existe desde la migración C1).
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.auth import TenantContext, get_usuario_actual, obtener_contexto_tenant_humano
from app.core.database import get_session
from app.core.rbac import requerir_gerencia_o_superadmin
from app.models.domain import Operario, Supervisor, UsuarioSaaS

router = APIRouter(prefix="/config", tags=["Personas"])


def _requerir_permiso_inactivos(incluir_inactivos: bool, usuario: UsuarioSaaS):
    if incluir_inactivos and usuario.rol.value not in ("superadmin", "gerencia"):
        raise HTTPException(status_code=403, detail="Sólo Gerencia o SuperAdmin pueden ver registros inactivos.")


class OperarioCreate(BaseModel):
    legajo: str
    nombre_completo: str


class OperarioUpdate(BaseModel):
    legajo: Optional[str] = None
    nombre_completo: Optional[str] = None
    activo: Optional[bool] = None


class SupervisorCreate(BaseModel):
    legajo: str
    nombre_completo: str


class SupervisorUpdate(BaseModel):
    legajo: Optional[str] = None
    nombre_completo: Optional[str] = None
    activo: Optional[bool] = None


# ==========================================
# OPERARIOS
# ==========================================
@router.post("/operarios/", response_model=Operario, status_code=status.HTTP_201_CREATED)
def crear_operario(
    payload: OperarioCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    existente = db.exec(
        select(Operario).where(
            Operario.tenant_id == context.tenant_id,
            Operario.legajo == payload.legajo,
            Operario.activo == True,  # noqa: E712
        )
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail=f"Ya existe un operario activo con legajo '{payload.legajo}'.")

    nuevo = Operario(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


@router.get("/operarios/", response_model=List[Operario])
def listar_operarios(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(Operario).where(Operario.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(Operario.activo == True)  # noqa: E712
    return db.exec(query).all()


@router.get("/operarios/{operario_id}", response_model=Operario)
def obtener_operario(
    operario_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    operario = db.exec(select(Operario).where(Operario.id == operario_id, Operario.tenant_id == context.tenant_id)).first()
    if not operario:
        raise HTTPException(status_code=404, detail="Operario no encontrado.")
    return operario


@router.patch("/operarios/{operario_id}", response_model=Operario)
def actualizar_operario(
    operario_id: uuid.UUID,
    payload: OperarioUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    operario = db.exec(select(Operario).where(Operario.id == operario_id, Operario.tenant_id == context.tenant_id)).first()
    if not operario:
        raise HTTPException(status_code=404, detail="Operario no encontrado.")

    datos = payload.model_dump(exclude_unset=True)

    reactivando = datos.get("activo") is True and not operario.activo
    legajo_nuevo = datos.get("legajo", operario.legajo)
    if reactivando or "legajo" in datos:
        conflicto = db.exec(
            select(Operario).where(
                Operario.tenant_id == context.tenant_id,
                Operario.legajo == legajo_nuevo,
                Operario.activo == True,  # noqa: E712
                Operario.id != operario_id,
            )
        ).first()
        if conflicto:
            raise HTTPException(status_code=409, detail=f"Ya existe un operario activo con legajo '{legajo_nuevo}'.")

    for key, value in datos.items():
        setattr(operario, key, value)
    db.add(operario)
    db.commit()
    db.refresh(operario)
    return operario


@router.delete("/operarios/{operario_id}")
def desactivar_operario(
    operario_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    operario = db.exec(select(Operario).where(Operario.id == operario_id, Operario.tenant_id == context.tenant_id)).first()
    if not operario:
        raise HTTPException(status_code=404, detail="Operario no encontrado.")
    operario.activo = False
    db.add(operario)
    db.commit()
    return {"mensaje": "Operario desactivado."}


# ==========================================
# SUPERVISORES
# ==========================================
@router.post("/supervisores/", response_model=Supervisor, status_code=status.HTTP_201_CREATED)
def crear_supervisor(
    payload: SupervisorCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    existente = db.exec(
        select(Supervisor).where(
            Supervisor.tenant_id == context.tenant_id,
            Supervisor.legajo == payload.legajo,
            Supervisor.activo == True,  # noqa: E712
        )
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail=f"Ya existe un supervisor activo con legajo '{payload.legajo}'.")

    nuevo = Supervisor(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


@router.get("/supervisores/", response_model=List[Supervisor])
def listar_supervisores(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(Supervisor).where(Supervisor.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(Supervisor.activo == True)  # noqa: E712
    return db.exec(query).all()


@router.get("/supervisores/{supervisor_id}", response_model=Supervisor)
def obtener_supervisor(
    supervisor_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    supervisor = db.exec(select(Supervisor).where(Supervisor.id == supervisor_id, Supervisor.tenant_id == context.tenant_id)).first()
    if not supervisor:
        raise HTTPException(status_code=404, detail="Supervisor no encontrado.")
    return supervisor


@router.patch("/supervisores/{supervisor_id}", response_model=Supervisor)
def actualizar_supervisor(
    supervisor_id: uuid.UUID,
    payload: SupervisorUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    supervisor = db.exec(select(Supervisor).where(Supervisor.id == supervisor_id, Supervisor.tenant_id == context.tenant_id)).first()
    if not supervisor:
        raise HTTPException(status_code=404, detail="Supervisor no encontrado.")

    datos = payload.model_dump(exclude_unset=True)

    reactivando = datos.get("activo") is True and not supervisor.activo
    legajo_nuevo = datos.get("legajo", supervisor.legajo)
    if reactivando or "legajo" in datos:
        conflicto = db.exec(
            select(Supervisor).where(
                Supervisor.tenant_id == context.tenant_id,
                Supervisor.legajo == legajo_nuevo,
                Supervisor.activo == True,  # noqa: E712
                Supervisor.id != supervisor_id,
            )
        ).first()
        if conflicto:
            raise HTTPException(status_code=409, detail=f"Ya existe un supervisor activo con legajo '{legajo_nuevo}'.")

    for key, value in datos.items():
        setattr(supervisor, key, value)
    db.add(supervisor)
    db.commit()
    db.refresh(supervisor)
    return supervisor


@router.delete("/supervisores/{supervisor_id}")
def desactivar_supervisor(
    supervisor_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    supervisor = db.exec(select(Supervisor).where(Supervisor.id == supervisor_id, Supervisor.tenant_id == context.tenant_id)).first()
    if not supervisor:
        raise HTTPException(status_code=404, detail="Supervisor no encontrado.")
    supervisor.activo = False
    db.add(supervisor)
    db.commit()
    return {"mensaje": "Supervisor desactivado."}

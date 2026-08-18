"""Billing MVP + Planes Comerciales (PRD v2.0). Fase EB: catálogo global
(módulos, planes de precio, métodos de pago) -- CRUD completo, exclusivo
SuperAdmin ("Panel SaaS -> Configuración"). Las tablas siguientes del PRD
(planes_comerciales, tenant_modulos_asignados, facturas, pagos_informados,
tenant_suscripcion) llegan en fases aparte (EC-EE), en este mismo router.

Reglas de negocio confirmadas con el usuario (AskUserQuestion, batch
anterior a esta fase):
- `modulos_disponibles` REEMPLAZA a MODULE_CATALOG (frontend) y
  MODULOS_VALIDOS (admin.py) como fuente de verdad del catálogo de
  módulos -- ver la validación actualizada en admin.py::
  actualizar_modulos_tenant.
- Nada de esta fase genera ni envía documentos/emails reales -- el
  sistema sólo calcula y deja registro (ver fases ED/EE, "factura" es
  un cálculo interno, nunca un PDF ni un email real).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel, field_validator
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
import uuid

from app.core.database import get_session
from app.core.rbac import requerir_superadmin
from app.models.domain import (
    ModuloDisponible, EstadoModuloDisponible,
    PlanPrecio, EstadoPlanPrecio,
    MetodoPagoConfigurado, EstadoMetodoPago,
    UsuarioSaaS,
)

router = APIRouter(prefix="/billing", tags=["Billing (SuperAdmin)"])


# ==========================================
# MÓDULOS DISPONIBLES
# ==========================================
class ModuloDisponibleCreate(BaseModel):
    codigo: str
    nombre: str
    descripcion: Optional[str] = None
    orden: int = 0
    estado: EstadoModuloDisponible = EstadoModuloDisponible.PROXIMAMENTE

    @field_validator("codigo")
    @classmethod
    def _normalizar_codigo(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("codigo no puede estar vacío.")
        return v


class ModuloDisponibleUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    orden: Optional[int] = None
    estado: Optional[EstadoModuloDisponible] = None


@router.get("/modulos", response_model=List[ModuloDisponible])
def listar_modulos_disponibles(
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    return db.exec(select(ModuloDisponible).order_by(ModuloDisponible.orden)).all()


@router.post("/modulos", response_model=ModuloDisponible, status_code=status.HTTP_201_CREATED)
def crear_modulo_disponible(
    payload: ModuloDisponibleCreate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if db.exec(select(ModuloDisponible).where(ModuloDisponible.codigo == payload.codigo)).first():
        raise HTTPException(status_code=409, detail=f"Ya existe un módulo con código '{payload.codigo}'.")
    modulo = ModuloDisponible(**payload.model_dump(), creado_por_id=usuario.id, actualizado_por_id=usuario.id)
    db.add(modulo)
    db.commit()
    db.refresh(modulo)
    return modulo


@router.put("/modulos/{modulo_id}", response_model=ModuloDisponible)
def actualizar_modulo_disponible(
    modulo_id: uuid.UUID,
    payload: ModuloDisponibleUpdate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    modulo = db.get(ModuloDisponible, modulo_id)
    if not modulo:
        raise HTTPException(status_code=404, detail="Módulo no encontrado.")
    datos = payload.model_dump(exclude_unset=True)
    for campo, valor in datos.items():
        setattr(modulo, campo, valor)
    modulo.actualizado_por_id = usuario.id
    modulo.actualizado_at = datetime.utcnow()
    db.add(modulo)
    db.commit()
    db.refresh(modulo)
    return modulo


@router.delete("/modulos/{modulo_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_modulo_disponible(
    modulo_id: uuid.UUID,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """PRD (criterios de aceptación): "eliminar (sin planes = OK)" --
    hard-delete real, pero SÓLO si nada depende del módulo todavía (ver
    principio de este backend de nunca borrar una entidad referenciada,
    ya aplicado en category/mapping_rule-equivalentes de este esquema:
    Turno, Estacion, etc. -- nunca DELETE, sólo archivar). Un módulo
    SIN ningún PlanPrecio (y, en fases siguientes, sin asignaciones a
    tenants) es seguro de borrar de verdad -- no hay nada de qué perder
    trazabilidad."""
    modulo = db.get(ModuloDisponible, modulo_id)
    if not modulo:
        raise HTTPException(status_code=404, detail="Módulo no encontrado.")
    tiene_planes = db.exec(select(PlanPrecio).where(PlanPrecio.modulo_id == modulo_id)).first()
    if tiene_planes:
        raise HTTPException(
            status_code=409,
            detail="No se puede eliminar: el módulo tiene planes de precio asociados. Eliminá los planes primero.",
        )
    db.delete(modulo)
    db.commit()


# ==========================================
# PLANES DE PRECIO (por módulo)
# ==========================================
class PlanPrecioCreate(BaseModel):
    modulo_id: uuid.UUID
    codigo: str
    nombre: str
    descripcion: Optional[str] = None
    precio: Decimal
    orden: int = 0
    limite_usuarios: Optional[int] = None
    limite_plantas: Optional[int] = None
    limite_lineas: Optional[int] = None
    estado: EstadoPlanPrecio = EstadoPlanPrecio.ACTIVO

    @field_validator("precio")
    @classmethod
    def _validar_precio(cls, v: Decimal) -> Decimal:
        # PRD: "Precio debe ser >= 0 (0 para Free, > 0 para pagos)".
        if v < 0:
            raise ValueError("precio no puede ser negativo.")
        return v

    @field_validator("codigo")
    @classmethod
    def _normalizar_codigo(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("codigo no puede estar vacío.")
        return v


class PlanPrecioUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    precio: Optional[Decimal] = None
    orden: Optional[int] = None
    limite_usuarios: Optional[int] = None
    limite_plantas: Optional[int] = None
    limite_lineas: Optional[int] = None
    estado: Optional[EstadoPlanPrecio] = None

    @field_validator("precio")
    @classmethod
    def _validar_precio(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("precio no puede ser negativo.")
        return v


@router.get("/modulos/{modulo_id}/planes", response_model=List[PlanPrecio])
def listar_planes_de_modulo(
    modulo_id: uuid.UUID,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if not db.get(ModuloDisponible, modulo_id):
        raise HTTPException(status_code=404, detail="Módulo no encontrado.")
    return db.exec(
        select(PlanPrecio).where(PlanPrecio.modulo_id == modulo_id).order_by(PlanPrecio.orden)
    ).all()


@router.post("/modulos/{modulo_id}/planes", response_model=PlanPrecio, status_code=status.HTTP_201_CREATED)
def crear_plan_precio(
    modulo_id: uuid.UUID,
    payload: PlanPrecioCreate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if not db.get(ModuloDisponible, modulo_id):
        raise HTTPException(status_code=404, detail="Módulo no encontrado.")
    if payload.modulo_id != modulo_id:
        raise HTTPException(status_code=400, detail="modulo_id del payload no coincide con la URL.")
    ya_existe = db.exec(
        select(PlanPrecio).where(PlanPrecio.modulo_id == modulo_id, PlanPrecio.codigo == payload.codigo)
    ).first()
    if ya_existe:
        raise HTTPException(status_code=409, detail=f"Ya existe un plan con código '{payload.codigo}' en este módulo.")
    plan = PlanPrecio(**payload.model_dump(), creado_por_id=usuario.id, actualizado_por_id=usuario.id)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.put("/planes/{plan_id}", response_model=PlanPrecio)
def actualizar_plan_precio(
    plan_id: uuid.UUID,
    payload: PlanPrecioUpdate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    plan = db.get(PlanPrecio, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado.")
    datos = payload.model_dump(exclude_unset=True)
    for campo, valor in datos.items():
        setattr(plan, campo, valor)
    plan.actualizado_por_id = usuario.id
    plan.actualizado_at = datetime.utcnow()
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/planes/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_plan_precio(
    plan_id: uuid.UUID,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """PRD: "eliminar (sin clientes = OK)". La verificación real de
    "sin clientes" (tenant_modulos_asignados) se agrega en Fase EC,
    cuando esa tabla exista -- hoy no hay ningún consumidor real que
    pueda referenciar un PlanPrecio todavía, así que el hard-delete es
    seguro sin esa validación adicional."""
    plan = db.get(PlanPrecio, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado.")
    db.delete(plan)
    db.commit()


# ==========================================
# MÉTODOS DE PAGO
# ==========================================
class MetodoPagoCreate(BaseModel):
    codigo: str
    nombre: str
    tipo: str
    detalle: Optional[str] = None
    orden: int = 0
    estado: EstadoMetodoPago = EstadoMetodoPago.ACTIVO

    @field_validator("codigo")
    @classmethod
    def _normalizar_codigo(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("codigo no puede estar vacío.")
        return v


class MetodoPagoUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[str] = None
    detalle: Optional[str] = None
    orden: Optional[int] = None
    estado: Optional[EstadoMetodoPago] = None


@router.get("/metodos-pago", response_model=List[MetodoPagoConfigurado])
def listar_metodos_pago(
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    return db.exec(select(MetodoPagoConfigurado).order_by(MetodoPagoConfigurado.orden)).all()


@router.post("/metodos-pago", response_model=MetodoPagoConfigurado, status_code=status.HTTP_201_CREATED)
def crear_metodo_pago(
    payload: MetodoPagoCreate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if db.exec(select(MetodoPagoConfigurado).where(MetodoPagoConfigurado.codigo == payload.codigo)).first():
        raise HTTPException(status_code=409, detail=f"Ya existe un método de pago con código '{payload.codigo}'.")
    metodo = MetodoPagoConfigurado(**payload.model_dump(), creado_por_id=usuario.id, actualizado_por_id=usuario.id)
    db.add(metodo)
    db.commit()
    db.refresh(metodo)
    return metodo


@router.put("/metodos-pago/{metodo_id}", response_model=MetodoPagoConfigurado)
def actualizar_metodo_pago(
    metodo_id: uuid.UUID,
    payload: MetodoPagoUpdate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    metodo = db.get(MetodoPagoConfigurado, metodo_id)
    if not metodo:
        raise HTTPException(status_code=404, detail="Método de pago no encontrado.")
    datos = payload.model_dump(exclude_unset=True)
    for campo, valor in datos.items():
        setattr(metodo, campo, valor)
    metodo.actualizado_por_id = usuario.id
    metodo.actualizado_at = datetime.utcnow()
    db.add(metodo)
    db.commit()
    db.refresh(metodo)
    return metodo


@router.delete("/metodos-pago/{metodo_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_metodo_pago(
    metodo_id: uuid.UUID,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """PRD: "eliminar (sin uso = OK)". Igual que PlanPrecio: la
    validación real de "sin uso" (facturas/tenant_modulos_asignados
    referenciándolo) llega en fases posteriores, cuando esas tablas
    existan."""
    metodo = db.get(MetodoPagoConfigurado, metodo_id)
    if not metodo:
        raise HTTPException(status_code=404, detail="Método de pago no encontrado.")
    db.delete(metodo)
    db.commit()

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
from typing import List, Optional, Tuple
from datetime import datetime, date
from decimal import Decimal
import uuid

from app.core.database import get_session
from app.core.rbac import requerir_superadmin
from app.models.domain import (
    ModuloDisponible, EstadoModuloDisponible,
    PlanPrecio, EstadoPlanPrecio,
    MetodoPagoConfigurado, EstadoMetodoPago,
    PlanComercial, EstadoPlanComercial, PlanComercialModulo, PlanComercialPlan,
    AsignacionModuloTenant, EstadoAsignacionModulo,
    Tenant,
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


# ==========================================
# Fase EC: PLANES COMERCIALES (descuentos/bonificación)
# ==========================================
def _validar_reglas_descuento(
    es_bonificado: bool,
    meses_bonificados: Optional[int],
    descuento_porcentaje: Optional[Decimal],
) -> None:
    """PRD §4: es_bonificado y descuento_porcentaje son mutuamente
    excluyentes (radio button en el PRD) -- validado acá, no con un
    field_validator de Pydantic, porque depende de más de un campo a la
    vez (mismo criterio de Fase EB para validaciones cruzadas)."""
    if es_bonificado:
        if descuento_porcentaje is not None:
            raise HTTPException(status_code=422, detail="No se puede combinar es_bonificado=True con descuento_porcentaje.")
        if meses_bonificados is not None and meses_bonificados <= 0:
            raise HTTPException(status_code=422, detail="meses_bonificados debe ser positivo, o None para ilimitado.")
    else:
        if descuento_porcentaje is None:
            raise HTTPException(status_code=422, detail="descuento_porcentaje es requerido cuando es_bonificado=False.")
        if not (Decimal("0") < descuento_porcentaje <= Decimal("100")):
            raise HTTPException(status_code=422, detail="descuento_porcentaje debe ser mayor a 0 y hasta 100.")
        if meses_bonificados is not None:
            raise HTTPException(status_code=422, detail="meses_bonificados sólo aplica cuando es_bonificado=True.")


def _validar_modulos_existen(db: Session, modulos_ids: List[uuid.UUID]) -> None:
    encontrados = set(db.exec(select(ModuloDisponible.id).where(ModuloDisponible.id.in_(modulos_ids))).all())
    faltantes = set(modulos_ids) - encontrados
    if faltantes:
        raise HTTPException(status_code=422, detail=f"Módulos inexistentes: {sorted(str(m) for m in faltantes)}")


def _validar_planes_existen(db: Session, planes_ids: List[uuid.UUID]) -> None:
    encontrados = set(db.exec(select(PlanPrecio.id).where(PlanPrecio.id.in_(planes_ids))).all())
    faltantes = set(planes_ids) - encontrados
    if faltantes:
        raise HTTPException(status_code=422, detail=f"Planes de precio inexistentes: {sorted(str(p) for p in faltantes)}")


def _reemplazar_modulos_aplicables(db: Session, plan_comercial_id: uuid.UUID, modulos_ids: List[uuid.UUID]) -> None:
    for fila in db.exec(select(PlanComercialModulo).where(PlanComercialModulo.plan_comercial_id == plan_comercial_id)).all():
        db.delete(fila)
    for modulo_id in modulos_ids:
        db.add(PlanComercialModulo(plan_comercial_id=plan_comercial_id, modulo_id=modulo_id))


def _reemplazar_planes_aplicables(db: Session, plan_comercial_id: uuid.UUID, planes_ids: List[uuid.UUID]) -> None:
    for fila in db.exec(select(PlanComercialPlan).where(PlanComercialPlan.plan_comercial_id == plan_comercial_id)).all():
        db.delete(fila)
    for plan_id in planes_ids:
        db.add(PlanComercialPlan(plan_comercial_id=plan_comercial_id, plan_id=plan_id))


class PlanComercialCreate(BaseModel):
    codigo: str
    nombre: str
    descripcion: Optional[str] = None
    es_bonificado: bool = False
    meses_bonificados: Optional[int] = None
    descuento_porcentaje: Optional[Decimal] = None
    aplica_a_todos_modulos: bool = True
    aplica_a_todos_planes: bool = True
    # Sólo se usan (y son requeridos) cuando aplica_a_todos_x=False.
    modulos_ids: List[uuid.UUID] = []
    planes_ids: List[uuid.UUID] = []
    estado: EstadoPlanComercial = EstadoPlanComercial.ACTIVO
    fecha_inicio: date
    fecha_fin: Optional[date] = None

    @field_validator("codigo")
    @classmethod
    def _normalizar_codigo(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("codigo no puede estar vacío.")
        return v


class PlanComercialUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    es_bonificado: Optional[bool] = None
    meses_bonificados: Optional[int] = None
    descuento_porcentaje: Optional[Decimal] = None
    aplica_a_todos_modulos: Optional[bool] = None
    aplica_a_todos_planes: Optional[bool] = None
    modulos_ids: Optional[List[uuid.UUID]] = None
    planes_ids: Optional[List[uuid.UUID]] = None
    estado: Optional[EstadoPlanComercial] = None
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None


class PlanComercialOut(BaseModel):
    """PlanComercial + las listas de aplicabilidad resueltas -- el
    SQLModel de la tabla no incluye las M2M, y el frontend (Fase EG)
    necesita saber a qué módulos/planes aplica cada uno para armar la UI
    de asignación."""
    id: uuid.UUID
    codigo: str
    nombre: str
    descripcion: Optional[str]
    es_bonificado: bool
    meses_bonificados: Optional[int]
    descuento_porcentaje: Optional[Decimal]
    aplica_a_todos_modulos: bool
    aplica_a_todos_planes: bool
    estado: EstadoPlanComercial
    fecha_inicio: date
    fecha_fin: Optional[date]
    creado_por_id: Optional[uuid.UUID]
    creado_at: datetime
    actualizado_por_id: Optional[uuid.UUID]
    actualizado_at: datetime
    modulos_ids: List[uuid.UUID] = []
    planes_ids: List[uuid.UUID] = []


def _serializar_plan_comercial(db: Session, plan_comercial: PlanComercial) -> PlanComercialOut:
    modulos_ids = db.exec(
        select(PlanComercialModulo.modulo_id).where(PlanComercialModulo.plan_comercial_id == plan_comercial.id)
    ).all()
    planes_ids = db.exec(
        select(PlanComercialPlan.plan_id).where(PlanComercialPlan.plan_comercial_id == plan_comercial.id)
    ).all()
    return PlanComercialOut(**plan_comercial.model_dump(), modulos_ids=list(modulos_ids), planes_ids=list(planes_ids))


@router.get("/planes-comerciales", response_model=List[PlanComercialOut])
def listar_planes_comerciales(
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    planes = db.exec(select(PlanComercial).order_by(PlanComercial.fecha_inicio.desc())).all()
    return [_serializar_plan_comercial(db, p) for p in planes]


@router.post("/planes-comerciales", response_model=PlanComercialOut, status_code=status.HTTP_201_CREATED)
def crear_plan_comercial(
    payload: PlanComercialCreate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if db.exec(select(PlanComercial).where(PlanComercial.codigo == payload.codigo)).first():
        raise HTTPException(status_code=409, detail=f"Ya existe un plan comercial con código '{payload.codigo}'.")
    _validar_reglas_descuento(payload.es_bonificado, payload.meses_bonificados, payload.descuento_porcentaje)
    if not payload.aplica_a_todos_modulos:
        if not payload.modulos_ids:
            raise HTTPException(status_code=422, detail="modulos_ids es requerido cuando aplica_a_todos_modulos=False.")
        _validar_modulos_existen(db, payload.modulos_ids)
    if not payload.aplica_a_todos_planes:
        if not payload.planes_ids:
            raise HTTPException(status_code=422, detail="planes_ids es requerido cuando aplica_a_todos_planes=False.")
        _validar_planes_existen(db, payload.planes_ids)

    datos = payload.model_dump(exclude={"modulos_ids", "planes_ids"})
    plan_comercial = PlanComercial(**datos, creado_por_id=usuario.id, actualizado_por_id=usuario.id)
    db.add(plan_comercial)
    db.commit()
    db.refresh(plan_comercial)

    if not payload.aplica_a_todos_modulos:
        _reemplazar_modulos_aplicables(db, plan_comercial.id, payload.modulos_ids)
    if not payload.aplica_a_todos_planes:
        _reemplazar_planes_aplicables(db, plan_comercial.id, payload.planes_ids)
    db.commit()

    return _serializar_plan_comercial(db, plan_comercial)


@router.put("/planes-comerciales/{plan_comercial_id}", response_model=PlanComercialOut)
def actualizar_plan_comercial(
    plan_comercial_id: uuid.UUID,
    payload: PlanComercialUpdate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    plan_comercial = db.get(PlanComercial, plan_comercial_id)
    if not plan_comercial:
        raise HTTPException(status_code=404, detail="Plan comercial no encontrado.")

    datos = payload.model_dump(exclude_unset=True)
    modulos_ids = datos.pop("modulos_ids", None)
    planes_ids = datos.pop("planes_ids", None)
    for campo, valor in datos.items():
        setattr(plan_comercial, campo, valor)

    _validar_reglas_descuento(plan_comercial.es_bonificado, plan_comercial.meses_bonificados, plan_comercial.descuento_porcentaje)

    if not plan_comercial.aplica_a_todos_modulos:
        ids_finales = modulos_ids if modulos_ids is not None else [
            r for r in db.exec(select(PlanComercialModulo.modulo_id).where(PlanComercialModulo.plan_comercial_id == plan_comercial_id)).all()
        ]
        if not ids_finales:
            raise HTTPException(status_code=422, detail="modulos_ids es requerido cuando aplica_a_todos_modulos=False.")
        _validar_modulos_existen(db, ids_finales)
        if modulos_ids is not None:
            _reemplazar_modulos_aplicables(db, plan_comercial_id, ids_finales)
    elif modulos_ids is not None or "aplica_a_todos_modulos" in datos:
        _reemplazar_modulos_aplicables(db, plan_comercial_id, [])

    if not plan_comercial.aplica_a_todos_planes:
        ids_finales = planes_ids if planes_ids is not None else [
            r for r in db.exec(select(PlanComercialPlan.plan_id).where(PlanComercialPlan.plan_comercial_id == plan_comercial_id)).all()
        ]
        if not ids_finales:
            raise HTTPException(status_code=422, detail="planes_ids es requerido cuando aplica_a_todos_planes=False.")
        _validar_planes_existen(db, ids_finales)
        if planes_ids is not None:
            _reemplazar_planes_aplicables(db, plan_comercial_id, ids_finales)
    elif planes_ids is not None or "aplica_a_todos_planes" in datos:
        _reemplazar_planes_aplicables(db, plan_comercial_id, [])

    plan_comercial.actualizado_por_id = usuario.id
    plan_comercial.actualizado_at = datetime.utcnow()
    db.add(plan_comercial)
    db.commit()
    db.refresh(plan_comercial)
    return _serializar_plan_comercial(db, plan_comercial)


@router.delete("/planes-comerciales/{plan_comercial_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_plan_comercial(
    plan_comercial_id: uuid.UUID,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """PRD: "eliminar (sin clientes = OK)"."""
    plan_comercial = db.get(PlanComercial, plan_comercial_id)
    if not plan_comercial:
        raise HTTPException(status_code=404, detail="Plan comercial no encontrado.")
    tiene_clientes = db.exec(
        select(AsignacionModuloTenant).where(AsignacionModuloTenant.plan_comercial_id == plan_comercial_id)
    ).first()
    if tiene_clientes:
        raise HTTPException(status_code=409, detail="No se puede eliminar: el plan comercial tiene clientes asignados.")
    _reemplazar_modulos_aplicables(db, plan_comercial_id, [])
    _reemplazar_planes_aplicables(db, plan_comercial_id, [])
    db.delete(plan_comercial)
    db.commit()


# ==========================================
# Fase EC: ASIGNACIÓN módulo+plan+descuento A UN TENANT
# ==========================================
def _validar_aplicabilidad_plan_comercial(
    db: Session, plan_comercial: PlanComercial, modulo_id: uuid.UUID, plan_id: uuid.UUID,
) -> None:
    if plan_comercial.estado != EstadoPlanComercial.ACTIVO:
        raise HTTPException(status_code=422, detail="El plan comercial no está activo.")
    if not plan_comercial.aplica_a_todos_modulos:
        aplica = db.exec(
            select(PlanComercialModulo).where(
                PlanComercialModulo.plan_comercial_id == plan_comercial.id,
                PlanComercialModulo.modulo_id == modulo_id,
            )
        ).first()
        if not aplica:
            raise HTTPException(status_code=422, detail="El plan comercial no aplica a este módulo.")
    if not plan_comercial.aplica_a_todos_planes:
        aplica = db.exec(
            select(PlanComercialPlan).where(
                PlanComercialPlan.plan_comercial_id == plan_comercial.id,
                PlanComercialPlan.plan_id == plan_id,
            )
        ).first()
        if not aplica:
            raise HTTPException(status_code=422, detail="El plan comercial no aplica a este plan de precio.")


def _resolver_precios(plan: PlanPrecio, plan_comercial: Optional[PlanComercial]) -> Tuple[Decimal, Decimal]:
    """precio_base = precio de lista del PlanPrecio. precio_con_descuento
    aplica el PlanComercial (si hay uno): 0 si es_bonificado (PRD §8,
    "100% bonificado ilimitado = deuda $0, estado 'al día' siempre" -- la
    ventana real de "cuántos meses lleva bonificado" es un cálculo
    temporal de Fase ED, no de acá), o precio_base menos el porcentaje
    si es descuento parcial. Sin plan_comercial, son iguales."""
    precio_base = plan.precio
    if plan_comercial is None:
        return precio_base, precio_base
    if plan_comercial.es_bonificado:
        return precio_base, Decimal("0.00")
    descuento = plan_comercial.descuento_porcentaje or Decimal("0")
    factor = (Decimal("100") - descuento) / Decimal("100")
    precio_con_descuento = (precio_base * factor).quantize(Decimal("0.01"))
    return precio_base, precio_con_descuento


class AsignacionModuloCreate(BaseModel):
    modulo_id: uuid.UUID
    plan_id: uuid.UUID
    plan_comercial_id: Optional[uuid.UUID] = None
    metodo_pago_id: uuid.UUID
    fecha_inicio: date
    fecha_renovacion: date
    estado: EstadoAsignacionModulo = EstadoAsignacionModulo.ACTIVA


class AsignacionModuloUpdate(BaseModel):
    plan_id: Optional[uuid.UUID] = None
    plan_comercial_id: Optional[uuid.UUID] = None
    metodo_pago_id: Optional[uuid.UUID] = None
    fecha_renovacion: Optional[date] = None
    estado: Optional[EstadoAsignacionModulo] = None


@router.get("/clientes/{tenant_id}/modulos", response_model=List[AsignacionModuloTenant])
def listar_modulos_de_tenant(
    tenant_id: str,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if not db.get(Tenant, tenant_id):
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    return db.exec(select(AsignacionModuloTenant).where(AsignacionModuloTenant.tenant_id == tenant_id)).all()


@router.post("/clientes/{tenant_id}/modulos", response_model=AsignacionModuloTenant, status_code=status.HTTP_201_CREATED)
def asignar_modulo_a_tenant(
    tenant_id: str,
    payload: AsignacionModuloCreate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if not db.get(Tenant, tenant_id):
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    if not db.get(ModuloDisponible, payload.modulo_id):
        raise HTTPException(status_code=404, detail="Módulo no encontrado.")
    plan = db.get(PlanPrecio, payload.plan_id)
    if not plan or plan.modulo_id != payload.modulo_id:
        raise HTTPException(status_code=422, detail="El plan no pertenece al módulo indicado.")
    if not db.get(MetodoPagoConfigurado, payload.metodo_pago_id):
        raise HTTPException(status_code=404, detail="Método de pago no encontrado.")

    plan_comercial = None
    if payload.plan_comercial_id:
        plan_comercial = db.get(PlanComercial, payload.plan_comercial_id)
        if not plan_comercial:
            raise HTTPException(status_code=404, detail="Plan comercial no encontrado.")
        _validar_aplicabilidad_plan_comercial(db, plan_comercial, payload.modulo_id, payload.plan_id)

    # UNIQUE(tenant_id, modulo_id) -- un tenant tiene UNA fila viva por
    # módulo (se edita con PUT, no se re-crea); chequeo previo para dar
    # un 409 legible en vez de la IntegrityError cruda de Postgres.
    ya_existe = db.exec(
        select(AsignacionModuloTenant).where(
            AsignacionModuloTenant.tenant_id == tenant_id,
            AsignacionModuloTenant.modulo_id == payload.modulo_id,
        )
    ).first()
    if ya_existe:
        raise HTTPException(status_code=409, detail="Este tenant ya tiene una asignación para este módulo. Usá PUT para editarla.")

    precio_base, precio_con_descuento = _resolver_precios(plan, plan_comercial)
    asignacion = AsignacionModuloTenant(
        tenant_id=tenant_id,
        modulo_id=payload.modulo_id,
        plan_id=payload.plan_id,
        plan_comercial_id=payload.plan_comercial_id,
        metodo_pago_id=payload.metodo_pago_id,
        fecha_inicio=payload.fecha_inicio,
        fecha_renovacion=payload.fecha_renovacion,
        precio_base=precio_base,
        precio_con_descuento=precio_con_descuento,
        estado=payload.estado,
        creado_por_id=usuario.id,
        actualizado_por_id=usuario.id,
    )
    db.add(asignacion)
    db.commit()
    db.refresh(asignacion)
    return asignacion


@router.put("/clientes/{tenant_id}/modulos/{asignacion_id}", response_model=AsignacionModuloTenant)
def actualizar_asignacion_modulo(
    tenant_id: str,
    asignacion_id: uuid.UUID,
    payload: AsignacionModuloUpdate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    asignacion = db.get(AsignacionModuloTenant, asignacion_id)
    if not asignacion or asignacion.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")

    datos = payload.model_dump(exclude_unset=True)

    plan_id_final = datos.get("plan_id", asignacion.plan_id)
    plan = db.get(PlanPrecio, plan_id_final)
    if not plan or plan.modulo_id != asignacion.modulo_id:
        raise HTTPException(status_code=422, detail="El plan no pertenece al módulo de esta asignación.")

    plan_comercial_cambia = "plan_comercial_id" in datos
    plan_comercial_id_final = datos.get("plan_comercial_id", asignacion.plan_comercial_id)
    plan_comercial = None
    if plan_comercial_id_final:
        plan_comercial = db.get(PlanComercial, plan_comercial_id_final)
        if not plan_comercial:
            raise HTTPException(status_code=404, detail="Plan comercial no encontrado.")
        if plan_comercial_cambia:
            _validar_aplicabilidad_plan_comercial(db, plan_comercial, asignacion.modulo_id, plan_id_final)

    if "metodo_pago_id" in datos and not db.get(MetodoPagoConfigurado, datos["metodo_pago_id"]):
        raise HTTPException(status_code=404, detail="Método de pago no encontrado.")

    for campo, valor in datos.items():
        setattr(asignacion, campo, valor)

    # PRD: "Recalcula al cambiar" -- pero SÓLO cuando plan o plan
    # comercial cambian de verdad (ver comentario de precio_base/
    # precio_con_descuento en el modelo: son snapshots, no se
    # recalculan solos por un cambio no relacionado como metodo_pago_id).
    if "plan_id" in datos or plan_comercial_cambia:
        asignacion.precio_base, asignacion.precio_con_descuento = _resolver_precios(plan, plan_comercial)

    asignacion.actualizado_por_id = usuario.id
    asignacion.actualizado_at = datetime.utcnow()
    db.add(asignacion)
    db.commit()
    db.refresh(asignacion)
    return asignacion


@router.delete("/clientes/{tenant_id}/modulos/{asignacion_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_asignacion_modulo(
    tenant_id: str,
    asignacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    asignacion = db.get(AsignacionModuloTenant, asignacion_id)
    if not asignacion or asignacion.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")
    db.delete(asignacion)
    db.commit()

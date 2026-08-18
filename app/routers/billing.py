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
from sqlmodel import Session, select, func
from pydantic import BaseModel, field_validator
from typing import List, Optional, Tuple
from datetime import datetime, date, timedelta
from decimal import Decimal
import uuid

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext
from app.core.rbac import requerir_superadmin, requerir_gerencia_o_superadmin
from app.models.domain import (
    ModuloDisponible, EstadoModuloDisponible,
    PlanPrecio, EstadoPlanPrecio,
    MetodoPagoConfigurado, EstadoMetodoPago,
    PlanComercial, EstadoPlanComercial, PlanComercialModulo, PlanComercialPlan,
    AsignacionModuloTenant, EstadoAsignacionModulo,
    Factura, EstadoFactura,
    SuscripcionTenant, EstadoCuentaTenant,
    PagoInformado, EstadoPagoInformado,
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


@router.get("/mi-empresa/modulos", response_model=List[AsignacionModuloTenant])
def listar_mis_modulos(
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """Fase EJ: PRD, pantalla cliente "SUSCRIPCIÓN Y FACTURACIÓN" ->
    "MÓDULOS CONTRATADOS" (precio/descuento/renovación) -- distinto del
    viejo `Tenant.modulos_contratados` (CSV, sólo controla qué aparece
    en el sidebar, ver comentario en domain.py::SuscripcionTenant). No
    estaba en el PRD §9 (que sólo lista el admin-side), mismo criterio
    de completitud que motivó `informar_pago`/`solicitar_factura`."""
    return db.exec(select(AsignacionModuloTenant).where(AsignacionModuloTenant.tenant_id == context.tenant_id)).all()


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


# ==========================================
# Fase ED: FACTURAS (cálculo de monto a pagar) + ESTADO DE CUENTA
# ==========================================
MESES_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def _generar_numero_factura(db: Session, anio: int) -> str:
    """Numeración global secuencial por año (FC-2026-001, FC-2026-002...),
    tal como declara el DDL del PRD (`numero VARCHAR(50) UNIQUE`, sin
    scope por tenant) -- el mockup de UI del PRD (§5) muestra "FC-2026-001"
    repetido para dos clientes distintos, pero es texto de ejemplo de la
    maqueta, no una regla real; el esquema (fuente de verdad) es único a
    nivel global, así que la secuencia también lo es."""
    prefijo = f"FC-{anio}-"
    cantidad = db.exec(select(func.count(Factura.id)).where(Factura.numero.like(f"{prefijo}%"))).one()
    return f"{prefijo}{cantidad + 1:03d}"


def _recalcular_estado_cuenta(db: Session, tenant_id: str) -> SuscripcionTenant:
    """PRD §8 (`calcular_deuda_cliente`). El PRD suma `monto` de las
    facturas pendiente_envio/enviada/vencida y trata "100% bonificado
    ilimitado" como un caso especial que fuerza deuda=0 -- acá ese caso
    especial no hace falta: como `_resolver_precios` (Fase EC) ya deja
    `precio_con_descuento=0.00` para un plan comercial bonificado, la
    factura de ese módulo nace con monto=0 y por lo tanto estado='pagada'
    de entrada (ver _generar_factura) -- nunca entra a la suma de deuda.
    El resultado es el mismo que el caso especial del PRD, sin duplicar
    la regla acá."""
    facturas_pendientes = db.exec(
        select(Factura).where(
            Factura.tenant_id == tenant_id,
            Factura.estado.in_([EstadoFactura.PENDIENTE_ENVIO, EstadoFactura.ENVIADA, EstadoFactura.VENCIDA]),
        )
    ).all()
    deuda_total = sum((f.monto for f in facturas_pendientes), Decimal("0.00"))
    facturas_vencidas = sum(1 for f in facturas_pendientes if f.estado == EstadoFactura.VENCIDA)

    if deuda_total <= 0:
        estado_cuenta = EstadoCuentaTenant.AL_DIA
    elif facturas_vencidas == 0:
        estado_cuenta = EstadoCuentaTenant.CON_DEUDA
    else:
        estado_cuenta = EstadoCuentaTenant.VENCIDA

    suscripcion = db.exec(select(SuscripcionTenant).where(SuscripcionTenant.tenant_id == tenant_id)).first()
    if not suscripcion:
        suscripcion = SuscripcionTenant(tenant_id=tenant_id)
    suscripcion.deuda_total = deuda_total
    suscripcion.facturas_vencidas = facturas_vencidas
    suscripcion.estado_cuenta = estado_cuenta
    suscripcion.actualizado_at = datetime.utcnow()
    db.add(suscripcion)
    db.commit()
    db.refresh(suscripcion)
    return suscripcion


def _generar_factura(db: Session, tenant_id: str, asignacion: AsignacionModuloTenant, usuario: UsuarioSaaS) -> Factura:
    """Núcleo compartido por las dos acciones de generación (SuperAdmin
    admin-side y Gerencia "solicitar factura" client-side, PRD §9
    `POST /api/tenant/solicitar-factura`) -- mismo cálculo, distinto
    quién lo dispara. NUNCA un cron (ver comentario en el modelo Factura,
    domain.py)."""
    if asignacion.estado != EstadoAsignacionModulo.ACTIVA:
        raise HTTPException(status_code=422, detail="Sólo se puede generar factura para una asignación activa.")

    hoy = date.today()
    periodo = hoy.strftime("%Y-%m")
    ya_existe = db.exec(
        select(Factura).where(Factura.asignacion_id == asignacion.id, Factura.periodo == periodo)
    ).first()
    if ya_existe:
        raise HTTPException(status_code=409, detail=f"Ya existe una factura para este módulo en el período {periodo} ({ya_existe.numero}).")

    plan = db.get(PlanPrecio, asignacion.plan_id)
    monto = asignacion.precio_con_descuento  # snapshot ya resuelto en Fase EC, no se recalcula acá
    concepto = f"{plan.nombre if plan else 'Módulo'} - {MESES_ES[hoy.month]} {hoy.year}"

    factura = Factura(
        tenant_id=tenant_id,
        asignacion_id=asignacion.id,
        numero=_generar_numero_factura(db, hoy.year),
        periodo=periodo,
        fecha_emision=hoy,
        concepto=concepto,
        monto=monto,
        metodo_pago_id=asignacion.metodo_pago_id,
        # PRD §6 (pseudocódigo): "estado='pagada' if monto == 0 else 'pendiente_envio'".
        estado=EstadoFactura.PAGADA if monto == 0 else EstadoFactura.PENDIENTE_ENVIO,
        fecha_vencimiento=hoy + timedelta(days=30),
        creado_por_id=usuario.id,
        actualizado_por_id=usuario.id,
    )
    db.add(factura)
    db.commit()
    db.refresh(factura)
    _recalcular_estado_cuenta(db, tenant_id)
    # _recalcular_estado_cuenta hace su propio commit, que EXPIRA los
    # atributos de `factura` en esta misma sesión -- SQLModel/Pydantic v2
    # .model_dump() (lo que usa la serialización de response_model) NO
    # dispara el lazy-refresh de SQLAlchemy como sí lo hace un getattr
    # normal, así que sin este segundo refresh la respuesta HTTP sale
    # como `{}` en silencio (bug real, encontrado probando este endpoint
    # a mano). Refrescar de nuevo antes de devolver.
    db.refresh(factura)
    return factura


@router.post(
    "/clientes/{tenant_id}/modulos/{asignacion_id}/generar-factura",
    response_model=Factura, status_code=status.HTTP_201_CREATED,
)
def generar_factura_admin(
    tenant_id: str,
    asignacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """Generación manual desde el panel de administración (cualquier tenant)."""
    asignacion = db.get(AsignacionModuloTenant, asignacion_id)
    if not asignacion or asignacion.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")
    return _generar_factura(db, tenant_id, asignacion, usuario)


@router.post(
    "/mi-empresa/modulos/{asignacion_id}/solicitar-factura",
    response_model=Factura, status_code=status.HTTP_201_CREATED,
)
def solicitar_factura_cliente(
    asignacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """PRD §9 `POST /api/tenant/solicitar-factura` -- el "cliente solicita"
    del criterio de aceptación §10 es, en este backend, la propia
    Gerencia del tenant (o SuperAdmin en Modo Dios) pidiéndola desde "Mi
    Empresa"; nunca un cron. tenant_id sale del contexto autenticado, no
    de un parámetro que el cliente pueda falsear."""
    asignacion = db.get(AsignacionModuloTenant, asignacion_id)
    if not asignacion or asignacion.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")
    return _generar_factura(db, context.tenant_id, asignacion, usuario)


@router.get("/facturas", response_model=List[Factura])
def listar_facturas_todas(
    estado: Optional[EstadoFactura] = None,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """Fase EF (auditoría de completitud contra PRD §9): faltaba el
    equivalente de `GET /api/admin/facturas/solicitudes` -- la pantalla
    admin "FACTURAS - PENDIENTE ENVÍO" (§5) lista facturas de VARIOS
    clientes a la vez en una sola tabla; `listar_facturas_de_tenant`
    (scoped a un tenant) no cubre esa vista. Con `?estado=pendiente_envio`
    reproduce exactamente esa pantalla."""
    query = select(Factura)
    if estado is not None:
        query = query.where(Factura.estado == estado)
    return db.exec(query.order_by(Factura.fecha_emision.desc())).all()


@router.get("/clientes/{tenant_id}/facturas", response_model=List[Factura])
def listar_facturas_de_tenant(
    tenant_id: str,
    estado: Optional[EstadoFactura] = None,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if not db.get(Tenant, tenant_id):
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    query = select(Factura).where(Factura.tenant_id == tenant_id)
    if estado is not None:
        query = query.where(Factura.estado == estado)
    return db.exec(query.order_by(Factura.fecha_emision.desc())).all()


@router.get("/mi-empresa/facturas", response_model=List[Factura])
def listar_mis_facturas(
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """PRD: pantalla cliente "FACTURAS RECIENTES"."""
    return db.exec(
        select(Factura).where(Factura.tenant_id == context.tenant_id).order_by(Factura.fecha_emision.desc())
    ).all()


@router.post("/facturas/{factura_id}/marcar-enviada", response_model=Factura)
def marcar_factura_enviada(
    factura_id: uuid.UUID,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """PRD §5/§9: el PDF/envío real pasa 100% fuera del sistema (por mail
    entre las personas involucradas) -- esto sólo deja constancia de que
    ya se envió, y cuándo, y quién lo marcó."""
    factura = db.get(Factura, factura_id)
    if not factura:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")
    if factura.estado != EstadoFactura.PENDIENTE_ENVIO:
        raise HTTPException(status_code=422, detail=f"Sólo se puede marcar como enviada una factura pendiente de envío (estado actual: {factura.estado.value}).")
    factura.estado = EstadoFactura.ENVIADA
    factura.enviada_por_id = usuario.id
    factura.fecha_envio = datetime.utcnow()
    factura.actualizado_por_id = usuario.id
    factura.actualizado_at = datetime.utcnow()
    db.add(factura)
    db.commit()
    db.refresh(factura)
    return factura


@router.get("/clientes/{tenant_id}/estado-cuenta", response_model=SuscripcionTenant)
def obtener_estado_cuenta_admin(
    tenant_id: str,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    if not db.get(Tenant, tenant_id):
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    return _recalcular_estado_cuenta(db, tenant_id)


@router.get("/mi-empresa/estado-cuenta", response_model=SuscripcionTenant)
def obtener_mi_estado_cuenta(
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """PRD `GET /api/tenant/estado-cuenta`. Recalcula en el momento (no
    hay cron que lo mantenga fresco solo) -- barato: sólo suma las
    facturas ya generadas del propio tenant, sin recorrer otros tenants."""
    return _recalcular_estado_cuenta(db, context.tenant_id)


# ==========================================
# Fase EE: PAGOS INFORMADOS (autoinforme del cliente) + aprobación
# ==========================================
class PagoInformadoCreate(BaseModel):
    fecha_pago: date
    monto: Decimal
    referencia: Optional[str] = None
    comprobante_url: Optional[str] = None


class RechazoPagoPayload(BaseModel):
    motivo: str

    @field_validator("motivo")
    @classmethod
    def _validar_motivo(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("motivo es requerido para rechazar un pago.")
        return v


@router.post(
    "/mi-empresa/facturas/{factura_id}/informar-pago",
    response_model=PagoInformado, status_code=status.HTTP_201_CREATED,
)
def informar_pago(
    factura_id: uuid.UUID,
    payload: PagoInformadoCreate,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """PRD §7/§9: el "autoinforme del cliente" -- el PRD no lista este
    endpoint explícitamente en su índice §9 (sólo los 3 admin-side), pero
    la maqueta (§5, botón "[Informar pago]") y el propio nombre de esta
    fase lo requieren; ver comentario en el modelo PagoInformado."""
    factura = db.get(Factura, factura_id)
    if not factura or factura.tenant_id != context.tenant_id:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")
    if factura.estado == EstadoFactura.PAGADA:
        raise HTTPException(status_code=422, detail="Esta factura ya está pagada.")
    ya_pendiente = db.exec(
        select(PagoInformado).where(
            PagoInformado.factura_id == factura_id,
            PagoInformado.estado == EstadoPagoInformado.PENDIENTE_REVISION,
        )
    ).first()
    if ya_pendiente:
        raise HTTPException(status_code=409, detail="Ya hay un pago informado para esta factura pendiente de revisión.")

    pago = PagoInformado(
        factura_id=factura_id,
        tenant_id=context.tenant_id,
        fecha_pago=payload.fecha_pago,
        monto=payload.monto,
        referencia=payload.referencia,
        comprobante_url=payload.comprobante_url,
        creado_por_id=usuario.id,
        actualizado_por_id=usuario.id,
    )
    db.add(pago)
    db.commit()
    db.refresh(pago)
    return pago


@router.get("/pagos-informados", response_model=List[PagoInformado])
def listar_pagos_informados(
    estado: Optional[EstadoPagoInformado] = None,
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    query = select(PagoInformado)
    if estado is not None:
        query = query.where(PagoInformado.estado == estado)
    return db.exec(query.order_by(PagoInformado.creado_at.desc())).all()


@router.get("/mi-empresa/pagos-informados", response_model=List[PagoInformado])
def listar_mis_pagos_informados(
    db: Session = Depends(get_session),
    _usuario: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    return db.exec(
        select(PagoInformado).where(PagoInformado.tenant_id == context.tenant_id).order_by(PagoInformado.creado_at.desc())
    ).all()


@router.post("/pagos-informados/{pago_id}/aprobar", response_model=PagoInformado)
def aprobar_pago_informado(
    pago_id: uuid.UUID,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """PRD §7 "Al Aprobar": pago→aprobado, factura→pagada, deuda
    recalcula, estado de cuenta actualiza. (Envío de email excluido --
    nada de esta fase manda comunicaciones reales, ver docstring del
    módulo.)"""
    pago = db.get(PagoInformado, pago_id)
    if not pago:
        raise HTTPException(status_code=404, detail="Pago informado no encontrado.")
    if pago.estado != EstadoPagoInformado.PENDIENTE_REVISION:
        raise HTTPException(status_code=422, detail=f"Sólo se puede aprobar un pago pendiente de revisión (estado actual: {pago.estado.value}).")

    factura = db.get(Factura, pago.factura_id)
    if not factura:
        raise HTTPException(status_code=404, detail="La factura de este pago ya no existe.")

    pago.estado = EstadoPagoInformado.APROBADO
    pago.aprobado_por_id = usuario.id
    pago.fecha_aprobacion = datetime.utcnow()
    pago.actualizado_por_id = usuario.id
    pago.actualizado_at = datetime.utcnow()
    db.add(pago)

    factura.estado = EstadoFactura.PAGADA
    factura.actualizado_por_id = usuario.id
    factura.actualizado_at = datetime.utcnow()
    db.add(factura)
    db.commit()

    _recalcular_estado_cuenta(db, pago.tenant_id)
    # el commit de _recalcular_estado_cuenta expira los atributos de
    # `pago` en esta sesión -- mismo bug/fix que en _generar_factura
    # (Fase ED, ver comentario ahí para el detalle completo).
    db.refresh(pago)
    return pago


@router.post("/pagos-informados/{pago_id}/rechazar", response_model=PagoInformado)
def rechazar_pago_informado(
    pago_id: uuid.UUID,
    payload: RechazoPagoPayload,
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(requerir_superadmin),
):
    """PRD §7 "Al Rechazar": pago→rechazado + motivo. La factura NO se
    toca -- ya estaba en un estado no-pagado (pendiente_envio/enviada)
    desde antes de informar el pago, y el pseudocódigo del PRD
    ("Factura -> 'pendiente'") no corresponde a ningún estado real del
    enum EstadoFactura; no hay nada que revertir."""
    pago = db.get(PagoInformado, pago_id)
    if not pago:
        raise HTTPException(status_code=404, detail="Pago informado no encontrado.")
    if pago.estado != EstadoPagoInformado.PENDIENTE_REVISION:
        raise HTTPException(status_code=422, detail=f"Sólo se puede rechazar un pago pendiente de revisión (estado actual: {pago.estado.value}).")

    pago.estado = EstadoPagoInformado.RECHAZADO
    pago.aprobado_por_id = usuario.id
    pago.fecha_aprobacion = datetime.utcnow()
    pago.observaciones = payload.motivo
    pago.actualizado_por_id = usuario.id
    pago.actualizado_at = datetime.utcnow()
    db.add(pago)
    db.commit()
    db.refresh(pago)
    return pago

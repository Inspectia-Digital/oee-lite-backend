"""Fase FA.4.2 (PRD Segmentación de Planes): resolución de qué tiene
contratado un tenant.

Dos mecanismos DELIBERADAMENTE SEPARADOS (PRD §3.3, decisión explícita
del documento de negocio):

  A) `submodulos_efectivos_tenant()` -- ¿a qué pantallas accede?
  B) `limite_efectivo()` / `puede_crear_usuario()` -- ¿hay cupo para un
     usuario/planta/línea más? (Fase FA.4.3)

Nunca se resuelven con la misma función ni se consultan entre sí:
contratar un add-on habilita una pantalla pero NO agrega cupo de
usuarios, y viceversa. Hay un test explícito de eso.

Nota sobre el nivel de detalle: el PRD llama "submódulo" a lo que en
este código es `CaracteristicaModulo` -- son la misma entidad, ver el
docstring de esa clase en domain.py.
"""
from typing import Optional, Set

from sqlmodel import Session, select

from app.models.domain import (
    AsignacionModuloTenant, CaracteristicaModulo, EstadoAsignacionModulo,
    PlanCaracteristica, PlanPrecio, TenantAddonContratado,
)

ESTADO_ADDON_ACTIVO = "activa"


def planes_activos_tenant(db: Session, tenant_id: str) -> list[PlanPrecio]:
    """Los PlanPrecio que el tenant tiene contratados y ACTIVOS.

    `AsignacionModuloTenant` es lo que el PRD llama `TenantPlanContratado`
    (no existe una tabla con ese nombre; ésta ya cumple ese rol desde
    Fase EC: tenant_id + plan_id + estado)."""
    return db.exec(
        select(PlanPrecio)
        .join(AsignacionModuloTenant, AsignacionModuloTenant.plan_id == PlanPrecio.id)
        .where(
            AsignacionModuloTenant.tenant_id == tenant_id,
            AsignacionModuloTenant.estado == EstadoAsignacionModulo.ACTIVA,
        )
    ).all()


def submodulos_efectivos_tenant(db: Session, tenant_id: str) -> Set[str]:
    """Códigos de submódulo a los que este tenant tiene acceso: unión de
    los que traen sus planes activos MÁS los contratados sueltos como
    add-on. Un mismo submódulo puede llegar por cualquiera de los dos
    caminos, indistintamente (PRD §3.1).

    GATE ESTRICTO, sin fallback (decisión del usuario): un tenant sin
    planes ni add-ons devuelve conjunto vacío -- no se le abre todo "por
    las dudas". Los tenants que ya operaban antes de esta fase reciben
    su asignación explícita en el seed (ver seed.py), que es dato real y
    auditable, en vez de una excepción escondida en el código."""
    desde_planes = db.exec(
        select(CaracteristicaModulo.codigo)
        .join(PlanCaracteristica, PlanCaracteristica.caracteristica_id == CaracteristicaModulo.id)
        .join(AsignacionModuloTenant, AsignacionModuloTenant.plan_id == PlanCaracteristica.plan_id)
        .where(
            AsignacionModuloTenant.tenant_id == tenant_id,
            AsignacionModuloTenant.estado == EstadoAsignacionModulo.ACTIVA,
        )
    ).all()

    desde_addons = db.exec(
        select(CaracteristicaModulo.codigo)
        .join(TenantAddonContratado, TenantAddonContratado.caracteristica_id == CaracteristicaModulo.id)
        .where(
            TenantAddonContratado.tenant_id == tenant_id,
            TenantAddonContratado.estado == ESTADO_ADDON_ACTIVO,
        )
    ).all()

    return set(desde_planes) | set(desde_addons)


def limite_efectivo(db: Session, tenant_id: str, campo: str) -> Optional[int]:
    """Suma el límite `campo` (limite_usuarios/limite_plantas/
    limite_lineas) de todos los planes activos del tenant.

    `None` = ILIMITADO: si CUALQUIER plan activo tiene ese eje en None,
    el conjunto es ilimitado (no se puede "sumar" un ilimitado a un
    número y obtener un tope). Un tenant sin ningún plan activo también
    devuelve None -- no tiene plan que le imponga un límite, y no es rol
    de esta función inventarle uno (ver FA.4.3 para el uso)."""
    planes = planes_activos_tenant(db, tenant_id)
    if not planes:
        return None

    valores = [getattr(p, campo) for p in planes]
    if any(v is None for v in valores):
        return None
    return sum(valores)

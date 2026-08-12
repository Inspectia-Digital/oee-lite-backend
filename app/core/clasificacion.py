"""Resolución de tiempo ideal y tolerancia para clasificar un evento
OPTIMO/LENTO/ALERTA (Fase S).

Extraído de scans.py -- antes esta lógica vivía inline en
registrar_escaneo_rapido() y sólo corría en el momento del ping. Fase S
necesita la MISMA lógica, con los MISMOS resultados, para poder
recomputar eventos ya persistidos con la config vigente (ver
app/routers/recomputo.py) -- de ahí que se factoree acá en vez de
reimplementarla: cualquier cambio a las cascadas de herencia (Fase Q/R)
tiene que aplicar igual a la ingesta en vivo y al recompute, nunca una
sola de las dos.
"""
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.models.domain import Estacion, Linea, MaestroSKU, SkuTiempoEstacion, Tenant

# Fase Q: default de sistema cuando ni la Estación ni su Línea configuraron
# umbrales -- son los mismos valores que antes eran el default duro de
# Estacion.umbral_optimo/lento/alerta (240/280/300).
UMBRAL_OPTIMO_DEFAULT_SISTEMA = 240
UMBRAL_LENTO_DEFAULT_SISTEMA = 280
UMBRAL_ALERTA_DEFAULT_SISTEMA = 300


def resolver_umbral(valor_estacion: Optional[int], valor_linea: Optional[int], default_sistema: int) -> int:
    """Cadena de herencia Estación > Línea > default de sistema (Fase Q)."""
    if valor_estacion is not None:
        return valor_estacion
    if valor_linea is not None:
        return valor_linea
    return default_sistema


def resolver_tolerancia(valor_estacion: Optional[float], valor_linea: Optional[float], default_tenant: float) -> float:
    """Misma cascada que resolver_umbral (Estación > Línea > default),
    separada porque acá el default no es una constante de sistema sino
    Tenant.tolerancia_lento_pct/alerta_pct (float, Fase R)."""
    if valor_estacion is not None:
        return valor_estacion
    if valor_linea is not None:
        return valor_linea
    return default_tenant


def validar_orden_lento_alerta(lento: Optional[float], alerta: Optional[float]) -> None:
    """Fase AB (hallazgo real revisando la cascada completa de umbrales/
    tolerancias a pedido de Green Mills): ni Linea/Estacion (Create y
    Update) ni Tenant (PATCH /mi-empresa/tenant) validaban que 'lento'
    fuera menor que 'alerta' -- ni en el schema Pydantic ni en el
    endpoint. Si quedan invertidos (ej. umbral_lento=300, umbral_alerta=
    200, o tolerancia_lento_pct=0.30 > tolerancia_alerta_pct=0.15),
    scans.py evalúa "delta_t > t_alerta" ANTES que "delta_t > t_lento"
    (ver clasificacion.py / registrar_escaneo_rapido): con alerta más
    chico que lento, todo lo que supera el umbral de lento YA superó
    antes el de alerta -- el nivel LENTO queda matemáticamente
    inalcanzable, en silencio, sin ningún error. La estación parece
    saltar directo de "óptimo" a "alerta" y nadie se entera de por qué.

    Sólo valida cuando LOS DOS están definidos como valores concretos en
    la misma fila (no intenta resolver la cascada de herencia completa
    Estación/Línea/Tenant en el momento de guardar) -- eso alcanza para
    atajar el error real de tipeo/carga sin tener que reconstruir toda
    la resolución en el momento del PATCH."""
    if lento is not None and alerta is not None and lento >= alerta:
        raise ValueError(
            f"El valor de 'lento' ({lento}) tiene que ser menor que el de 'alerta' ({alerta}) -- "
            "si no, el nivel LENTO nunca se alcanza (todo lo que supera 'lento' ya superó 'alerta' antes)."
        )


@dataclass
class UmbralesResueltos:
    """Resultado de resolver_umbrales_evento(): todo lo que hace falta
    para clasificar UN evento (t_optimo/t_lento/t_alerta) y, si el
    evento resolvió un SKU, el tiempo ideal de UN ciclo completo (ya
    multiplicado por las unidades del evento) para el cap de Rendimiento."""
    t_optimo: float
    t_lento: float
    t_alerta: float
    tiempo_ideal_por_ciclo: float
    sku_resuelto: Optional[MaestroSKU]


def resolver_umbrales_evento(
    db: Session,
    tenant_id: str,
    tenant_config: Optional[Tenant],
    estacion: Estacion,
    linea: Optional[Linea],
    sku_final: Optional[str],
    unidades_a_sumar: int,
) -> UmbralesResueltos:
    """Misma resolución que la sección 2/2.b de scans.py, factoreada:

    - Rama A (sin SKU resuelto): umbral_optimo/lento/alerta heredable
      Estación > Línea > default de sistema (segundos absolutos).
    - Rama B (con SKU resuelto): tiempo_ciclo_teorico del SKU, salvo que
      exista un override en SkuTiempoEstacion para este (SKU, Estación)
      puntual (Fase R); tolerancia % heredable Estación > Línea > Tenant.
    """
    t_optimo = resolver_umbral(estacion.umbral_optimo, linea.umbral_optimo if linea else None, UMBRAL_OPTIMO_DEFAULT_SISTEMA)
    t_lento = resolver_umbral(estacion.umbral_lento, linea.umbral_lento if linea else None, UMBRAL_LENTO_DEFAULT_SISTEMA)
    t_alerta = resolver_umbral(estacion.umbral_alerta, linea.umbral_alerta if linea else None, UMBRAL_ALERTA_DEFAULT_SISTEMA)

    sku_resuelto = None
    if sku_final:
        sku_resuelto = db.exec(
            select(MaestroSKU).where(MaestroSKU.codigo_sku == sku_final, MaestroSKU.tenant_id == tenant_id)
        ).first()
        if sku_resuelto:
            override_tiempo = db.exec(
                select(SkuTiempoEstacion).where(
                    SkuTiempoEstacion.tenant_id == tenant_id,
                    SkuTiempoEstacion.sku_fk == sku_resuelto.codigo_sku,
                    SkuTiempoEstacion.estacion_id == estacion.id,
                    SkuTiempoEstacion.activo == True,  # noqa: E712
                )
            ).first()
            tiempo_ciclo_efectivo = override_tiempo.tiempo_ciclo_teorico if override_tiempo else sku_resuelto.tiempo_ciclo_teorico
            t_optimo = tiempo_ciclo_efectivo

    if sku_resuelto:
        tiempo_ideal_por_ciclo = t_optimo * unidades_a_sumar
        tolerancia_lento_pct = resolver_tolerancia(
            estacion.tolerancia_lento_pct,
            linea.tolerancia_lento_pct if linea else None,
            tenant_config.tolerancia_lento_pct if tenant_config else 1.15,
        )
        tolerancia_alerta_pct = resolver_tolerancia(
            estacion.tolerancia_alerta_pct,
            linea.tolerancia_alerta_pct if linea else None,
            tenant_config.tolerancia_alerta_pct if tenant_config else 1.25,
        )
        t_lento = tiempo_ideal_por_ciclo * tolerancia_lento_pct
        t_alerta = tiempo_ideal_por_ciclo * tolerancia_alerta_pct
    else:
        tiempo_ideal_por_ciclo = t_optimo  # = umbral_optimo resuelto; usado en el cap de Rendimiento

    return UmbralesResueltos(
        t_optimo=t_optimo, t_lento=t_lento, t_alerta=t_alerta,
        tiempo_ideal_por_ciclo=tiempo_ideal_por_ciclo, sku_resuelto=sku_resuelto,
    )

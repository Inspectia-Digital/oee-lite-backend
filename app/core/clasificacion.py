"""Resolución de tiempo ideal y umbrales para clasificar un evento
OPTIMO/LENTO/ALERTA (Fase S, rediseñado en Fase AC).

Extraído de scans.py -- antes esta lógica vivía inline en
registrar_escaneo_rapido() y sólo corría en el momento del ping. Fase S
necesita la MISMA lógica, con los MISMOS resultados, para poder
recomputar eventos ya persistidos con la config vigente (ver
app/routers/recomputo.py) -- de ahí que se factoree acá en vez de
reimplementarla: cualquier cambio a la cascada de resolución tiene que
aplicar igual a la ingesta en vivo y al recompute, nunca una sola de
las dos.

Fase AC (pedido explícito de Green Mills tras usar el sistema): el
modelo de Fases Q/R -- 5 campos en Estación, 5 en Línea, 2 en Tenant,
mezclando segundos y porcentaje según si el evento resolvía SKU o no --
resultó "desprolijo, difícil de entender, propenso a errores". Se
reemplaza por UN solo concepto en TODA la cascada: un "perfil de
tiempos" son siempre 3 números en segundos (ideal, lento, alerta), que
viajan juntos. Nunca porcentaje, nunca un campo suelto. La cascada
pasa de 4 niveles (Estación/Línea/Tenant + rama SKU aparte) a 3, todos
resolviendo el mismo perfil de 3 campos:

    SKU × Estación  (override puntual, opcional, SIEMPRE completo)
        -> SKU genérico  (MaestroSKU, opcional -- incompleto = ausente)
            -> Línea  (piso obligatorio, siempre completo)

Estación y Empresa/Tenant dejan de ser niveles de la cascada. Estación
sigue existiendo como parte de la CLAVE del override más específico
(SKU×Estación), pero no tiene un perfil propio independiente de un SKU.
"""
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.models.domain import Estacion, EstadoOrden, EstadoPlan, Linea, MaestroSKU, OrdenProduccion, PlanProduccion, SkuTiempoEstacion


def validar_perfil_tiempos(ideal: float, lento: Optional[float], alerta: Optional[float]) -> None:
    """Fase AC (generaliza validar_orden_lento_alerta de Fase AB): un
    perfil de tiempos válido cumple ideal <= lento < alerta. Sólo valida
    cuando lento Y alerta están los dos presentes -- un perfil con sólo
    uno de los dos cargado es un perfil INCOMPLETO (ver
    resolver_umbrales_evento), no uno inválido; no bloquea la carga
    parcial, sólo ataja números concretos que quedaron mal cargados.

    Sin este chequeo, si alerta queda <= lento (ej. tipeo invertido),
    scans.py evalúa "delta_t > t_alerta" ANTES que "delta_t > t_lento"
    (ver más abajo / registrar_escaneo_rapido en scans.py): el nivel
    LENTO queda matemáticamente inalcanzable, en silencio -- la estación
    parece saltar directo de OPTIMO a ALERTA y nadie se entera de por qué.
    Este bug fue real en producción (Fase AB) antes de existir este chequeo."""
    if lento is None or alerta is None:
        return
    if ideal > lento:
        raise ValueError(
            f"El tiempo 'ideal' ({ideal}s) no puede ser mayor que 'lento' ({lento}s) -- "
            "un ciclo a ritmo ideal tiene que clasificar OPTIMO, nunca LENTO."
        )
    if lento >= alerta:
        raise ValueError(
            f"El tiempo 'lento' ({lento}s) tiene que ser menor que 'alerta' ({alerta}s) -- "
            "si no, el nivel LENTO nunca se alcanza (todo lo que supera 'lento' ya superó 'alerta' antes)."
        )


@dataclass
class UmbralesResueltos:
    """Resultado de resolver_umbrales_evento() para UN evento.

    t_optimo es el ideal POR UNIDAD, sin escalar -- así lo consume
    scans.py para el snapshot inmutable LiteEventoProduccion.tiempo_ideal_seg
    (Rendimiento se calcula aguas abajo como tiempo_ideal_seg *
    unidades_procesadas, ver analytics.py). t_lento/t_alerta y
    tiempo_ideal_por_ciclo SÍ vienen escalados por las unidades de ESTE
    evento -- son los umbrales absolutos contra los que se compara
    delta_t (segundos transcurridos desde el evento anterior)."""
    t_optimo: float
    t_lento: float
    t_alerta: float
    tiempo_ideal_por_ciclo: float
    sku_resuelto: Optional[MaestroSKU]


def _perfil_sku_completo(sku: MaestroSKU) -> Optional[tuple]:
    """Un SKU siempre tiene tiempo_ideal_seg (default 240.0), pero eso
    solo no alcanza para clasificar -- hace falta lento Y alerta. Si
    falta cualquiera de los dos, el perfil se trata como AUSENTE (nunca
    se mezcla el ideal del SKU con el lento/alerta de otra fuente)."""
    if sku.tiempo_lento_seg is None or sku.tiempo_alerta_seg is None:
        return None
    return (sku.tiempo_ideal_seg, sku.tiempo_lento_seg, sku.tiempo_alerta_seg)


def resolver_umbrales_evento(
    db: Session,
    tenant_id: str,
    estacion: Estacion,
    linea: Optional[Linea],
    sku_final: Optional[str],
    unidades_a_sumar: int,
) -> UmbralesResueltos:
    """Resuelve el perfil de tiempos (ideal, lento, alerta) para UN
    evento, cascada SKU×Estación -> SKU -> Línea (Fase AC):

    1. Si el evento resuelve un SKU y existe un override activo para
       (SKU, Estación), se usa ese perfil completo (los 3 campos son
       NOT NULL en SkuTiempoEstacion -- siempre está completo si existe).
    2. Si no hay override pero el SKU tiene su propio perfil completo
       (tiempo_lento_seg y tiempo_alerta_seg cargados), se usa ese.
    3. En cualquier otro caso (sin SKU resuelto, o SKU con perfil
       incompleto) se cae ENTERO al piso de Línea -- nunca se mezclan
       campos de dos fuentes distintas para el mismo evento.

    Escalado por unidades_a_sumar -- SÓLO cuando el perfil viene de un
    SKU (con o sin override): ahí ideal/lento/alerta son "por unidad" en
    un sentido real (un SKU define un ritmo de producción). El piso de
    Línea, en cambio, es "por EVENTO/scan" -- exactamente el mismo
    concepto que la vieja Rama A (Fase Q), que nunca escalaba. Regresión
    real encontrada con datos de Green Mills (Fase AC, antes de shippear):
    su ingesta PLC-ciega manda unidades_procesadas explícito (Fase P,
    conteo edge-autoritativo) SIN resolver ningún SKU -- escalar el piso
    de línea por esas unidades multiplicaba t_alerta x5, y una parada
    real de 240s dejaba de detectarse. t_optimo tampoco se escala nunca
    (se devuelve por unidad), porque scans.py lo persiste tal cual como
    snapshot inmutable del evento."""
    sku_resuelto = None
    perfil = None  # (ideal, lento, alerta) por unidad, o None si no hay perfil de SKU utilizable

    if sku_final:
        sku_resuelto = db.exec(
            select(MaestroSKU).where(MaestroSKU.codigo_sku == sku_final, MaestroSKU.tenant_id == tenant_id)
        ).first()
        if sku_resuelto:
            override = db.exec(
                select(SkuTiempoEstacion).where(
                    SkuTiempoEstacion.tenant_id == tenant_id,
                    SkuTiempoEstacion.sku_fk == sku_resuelto.codigo_sku,
                    SkuTiempoEstacion.estacion_id == estacion.id,
                    SkuTiempoEstacion.activo == True,  # noqa: E712
                )
            ).first()
            if override:
                perfil = (override.tiempo_ideal_seg, override.tiempo_lento_seg, override.tiempo_alerta_seg)
            else:
                perfil = _perfil_sku_completo(sku_resuelto)

    if perfil is not None:
        ideal, lento, alerta = perfil
        factor = unidades_a_sumar
    elif linea is not None:
        ideal, lento, alerta = linea.tiempo_ideal_seg, linea.tiempo_lento_seg, linea.tiempo_alerta_seg
        factor = 1  # piso de línea: umbral por EVENTO, no por unidad -- nunca se escala
    else:
        # Defensivo: una estación sin línea asociada es un estado
        # inconsistente que no debería darse en la práctica (Estacion.linea_id
        # es obligatoria desde la migración C1). No hay ningún otro piso
        # configurable por debajo de Línea -- este es el único lugar del
        # sistema con un valor fijo, y sólo se alcanza si ese invariante
        # se rompe.
        ideal, lento, alerta = 240.0, 280.0, 300.0
        factor = 1

    return UmbralesResueltos(
        t_optimo=ideal,
        t_lento=lento * factor,
        t_alerta=alerta * factor,
        tiempo_ideal_por_ciclo=ideal * factor,
        sku_resuelto=sku_resuelto,
    )


def resolver_orden_activa(db: Session, tenant_id: str, linea_id) -> Optional[OrdenProduccion]:
    """Fase AA: resuelve "cuál es la orden activa de esta línea AHORA
    MISMO", con la MISMA fuente de verdad para scans.py (ingesta) y
    /analytics/linea-en-vivo/ (lo que se ve en pantalla) -- antes cada
    uno reimplementaba su propia versión de "la orden EN_PROGRESO más
    reciente" por separado (Fase P), sin garantía de que dieran lo mismo.

    - Si la línea tiene un PlanProduccion ABIERTO con orden_activa_fk
      definida, esa es la fuente AUTORITATIVA -- la decide un supervisor
      (ver avanzar_orden en operacion.py), sin ambigüedad posible.
    - Si no (la línea no adoptó el flujo de Plan, o el plan no tiene
      ninguna orden activa todavía), cae al heurístico histórico: la
      orden EN_PROGRESO más reciente de la línea (comportamiento
      idéntico al de antes de Fase AA para cualquier línea que no cree
      un Plan -- Plan es opcional, nunca se le impone a un tenant)."""
    plan_abierto = db.exec(
        select(PlanProduccion).where(
            PlanProduccion.tenant_id == tenant_id,
            PlanProduccion.linea_id == linea_id,
            PlanProduccion.estado == EstadoPlan.ABIERTO,
            PlanProduccion.activo == True,  # noqa: E712
        )
    ).first()
    if plan_abierto and plan_abierto.orden_activa_fk:
        orden = db.exec(
            select(OrdenProduccion).where(
                OrdenProduccion.tenant_id == tenant_id,
                OrdenProduccion.id == plan_abierto.orden_activa_fk,
            )
        ).first()
        if orden:
            return orden

    return db.exec(
        select(OrdenProduccion)
        .where(
            OrdenProduccion.tenant_id == tenant_id,
            OrdenProduccion.linea_id == linea_id,
            OrdenProduccion.estado == EstadoOrden.EN_PROGRESO,
            OrdenProduccion.activo == True,  # noqa: E712
        )
        .order_by(OrdenProduccion.id_orden.desc())
    ).first()

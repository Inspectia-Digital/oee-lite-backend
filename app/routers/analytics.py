from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select
from sqlalchemy import cast, String  # <-- IMPORTANTE: Para el casteo de UUID a String
from datetime import datetime, time, date, timedelta
from typing import List, Optional
import uuid

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext
from app.models.domain import (
    Estacion, LiteEventoProduccion, ParadaDetectada,
    MotivoParada, Operario, Turno, Linea, TipoParada,
    Planta, UsuarioSaaS, RolUsuario, UsuarioPlanta, Tenant,
    OrdenProduccion, AsignacionTurno, EstadoParada, EstadoOrden,
)
from app.core.auth import get_usuario_actual
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Analytics"])

# ==========================================
# --- MOLDES (Schemas) ---
# ==========================================
class MetricasEstacion(BaseModel):
    estacion_nombre: str
    total_piezas: int
    optimos: int
    lentos: int
    alertas: int
    retrabajos: int
    tiempo_promedio_seg: float

class OeeGeneralCard(BaseModel):
    disponibilidad_pct: float
    rendimiento_pct: float
    calidad_pct: Optional[float] = None  # None = N/A (Fase E2): sin datos de calidad, no se reemplaza por 100% ni 0%.
    oee_general_pct: float
    total_unidades: int
    unidades_con_retrabajo: int  # Repurposed (Fase E2): suma de unidades_rechazadas (el estado "RETRABAJO" nunca lo setea el motor real).
    minutos_desvio_calidad: float

class ReporteOperarioSpringwall(BaseModel):
    operario_nombre: str
    estacion_nombre: str
    cantidad_real: int
    cantidad_esperada: int
    diferencia_pct: float

class ParetoParadas(BaseModel):
    motivo: str
    tipo: str
    frecuencia: int
    minutos_totales: float

class CuelloBotella(BaseModel):
    estacion: str
    tiempo_esperado_seg: float
    tiempo_promedio_real_seg: float
    desvio_pct: float

class AlertaActiva(BaseModel):
    hora: str
    estacion: str
    tipo: str 
    mensaje: str

class TendenciaOEERow(BaseModel):
    fecha: str
    oee: float
    disp: float
    rend: float
    # Fase O: None = sin datos de calidad ese día (distinto de 0%), mismo
    # criterio que OeeGeneralCard.calidad_pct.
    cal: Optional[float] = None

class CascadaOEE(BaseModel):
    tiempo_calendario_min: float
    tiempo_planificado_min: float
    tiempo_operativo_min: float
    tiempo_neto_min: float
    tiempo_efectivo_min: float

class RendimientoSecuencialRow(BaseModel):
    estacion: str
    posicion_linea: int
    tiempo_ciclo_prom: float
    objetivo: float

class ReporteProduccionRow(BaseModel):
    fecha: str
    estacion: str
    total_piezas: int
    optimos: int
    lentos: int
    alertas: int
    rechazadas: int
    tiempo_promedio_seg: float

class PlanVsActualRow(BaseModel):
    id_orden: str
    sku_fk: Optional[str] = None
    linea_id: uuid.UUID
    linea_nombre: str
    plan_fecha: Optional[str] = None
    estado: str
    cantidad_esperada: int
    cantidad_producida: int
    cumplimiento_pct: Optional[float] = None

class LineaEnVivoEstacion(BaseModel):
    estacion_fk: uuid.UUID
    estacion_nombre: str
    posicion_linea: int
    estado: str  # "activa" | "parada" | "sin_datos"
    operario_id: Optional[uuid.UUID] = None
    operario_nombre: Optional[str] = None
    ultimo_evento: Optional[str] = None

class LineaEnVivoResumen(BaseModel):
    linea_id: Optional[uuid.UUID] = None
    linea_nombre: Optional[str] = None
    turno_actual: Optional[str] = None
    orden_activa: Optional[str] = None
    orden_sku: Optional[str] = None
    estaciones: List[LineaEnVivoEstacion] = []

class RendimientoOperarioRow(BaseModel):
    operario_id: uuid.UUID
    legajo: str
    nombre: str
    estaciones_operadas: List[str]
    unidades_producidas: int
    retrabajos_generados: int
    tiempo_promedio_seg: float
    distribucion_desempeno: dict  # {"optimo": pct, "lento": pct, "alerta": pct}
    eficiencia_global: float  # % óptimo sobre el total de eventos

class CommandCenterPlanta(BaseModel):
    id: uuid.UUID
    nombre: str
    oee: Optional[float] = None
    estado: str

class CommandCenterInfraestructura(BaseModel):
    estaciones_activas: int
    estaciones_total: int

class CommandCenterSummary(BaseModel):
    oee_global: Optional[float] = None
    alertas_activas: int
    plantas: List[CommandCenterPlanta]
    infraestructura: CommandCenterInfraestructura

# ==========================================
# --- HELPER FUNCTIONS ---
# ==========================================
def obtener_rango_dia(fecha_busqueda: Optional[date] = None):
    f = fecha_busqueda or datetime.now().date()
    return datetime.combine(f, time.min), datetime.combine(f, time.max)

def validar_planta(context: TenantContext):
    """Firewall de OS Shell: Si no hay planta seleccionada, aborta la consulta silenciosamente."""
    if not context.sub_tenant_id:
        # Al lanzar un ValueError (y no un HTTPException), 
        # el bloque 'try/except' general de nuestros endpoints lo atrapará 
        # y devolverá la estructura vacía [] en lugar de un Error HTTP 400.
        raise ValueError("Planta no seleccionada. Retornando panel vacío.")


# ==========================================
# ENDPOINTS BLINDADOS (MULTI-TENANT & MULTI-PLANTA)
# ==========================================

@router.get("/reportes/dashboard", response_model=list[MetricasEstacion])
def obtener_dashboard_estaciones(
    skip: int = 0, limit: int = 1000, 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    try:
        validar_planta(context)
        inicio_dia, fin_dia = obtener_rango_dia()
        
        # 🔒 CAST EXPLÍCITO para cruzar String (id_estacion) con UUID (Estacion.id)
        resultados = db.exec(
            select(LiteEventoProduccion, Estacion)
            .join(Estacion, LiteEventoProduccion.id_estacion == cast(Estacion.id, String))
            .join(Linea, Estacion.linea_id == Linea.id)
            .where(
                LiteEventoProduccion.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                LiteEventoProduccion.timestamp >= inicio_dia,
                LiteEventoProduccion.timestamp <= fin_dia
            )
            .offset(skip)
            .limit(limit)
        ).all()

        data_agrupada = {}

        for evento, estacion in resultados:
            if estacion.nombre not in data_agrupada:
                data_agrupada[estacion.nombre] = {
                    "total": 0, "optimo": 0, "lento": 0, "alerta": 0, 
                    "retrabajo": 0, "suma_tiempos": 0, "eventos_con_tiempo": 0
                }
            
            m = data_agrupada[estacion.nombre]
            unidades = evento.unidades_procesadas
            m["total"] += unidades
            
            if evento.estado == "OPTIMO": m["optimo"] += unidades
            elif evento.estado == "LENTO": m["lento"] += unidades
            elif evento.estado == "ALERTA": m["alerta"] += unidades
            
            if evento.estado == "RETRABAJO":
                m["retrabajo"] += unidades
                
            if evento.delta_t_segundos and evento.delta_t_segundos > 0:
                m["suma_tiempos"] += evento.delta_t_segundos
                m["eventos_con_tiempo"] += 1

        reporte_final = []
        for nombre, metricas in data_agrupada.items():
            promedio = 0.0
            if metricas["eventos_con_tiempo"] > 0:
                promedio = round(metricas["suma_tiempos"] / metricas["eventos_con_tiempo"], 2)
                
            reporte_final.append(
                MetricasEstacion(
                    estacion_nombre=nombre, total_piezas=metricas["total"],
                    optimos=metricas["optimo"], lentos=metricas["lento"],
                    alertas=metricas["alerta"], retrabajos=metricas["retrabajo"],
                    tiempo_promedio_seg=promedio
                )
            )

        return reporte_final
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en dashboard: {e}")
        return []


@router.get("/analytics/oee-general/", response_model=OeeGeneralCard)
def obtener_oee_general(
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, turno_id: Optional[uuid.UUID] = None,
    orden_fk: Optional[str] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """
    Motor OEE (Fase E2). Fórmulas:
    - Rendimiento = (tiempo_ideal_seg * unidades_procesadas, sumado) / tiempo_operativo.
      tiempo_ideal_seg es un snapshot inmutable tomado al momento del
      escaneo (umbral del SKU activo si había uno, si no el de la
      estación) -- no se puede reconstruir retroactivamente qué SKU
      corría en un evento pasado, por eso el snapshot.
    - Disponibilidad = tiempo_operativo / tiempo_planificado_neto. Los
      "huecos" ya vienen correctamente recortados desde el escaneo
      (Fase E1/E2 en scans.py): ParadaDetectada.duracion_segundos es el
      EXCEDENTE sobre la tolerancia, no el delta completo.
    - Calidad combina dos métodos según Linea.metodo_calidad:
      POR_RECHAZO (unidades buenas / procesadas) y POR_TIEMPO (unidades
      dentro del umbral / inspeccionadas, sólo en estaciones tipo
      "calidad"). Si no hay datos de ningún método, calidad=N/A
      (None) y se excluye del producto OEE -- nunca se reemplaza por
      100% ni 0%.

    Limitación conocida y documentada: para Calidad por tiempo, "el
    umbral SKU prevalece sobre el umbral de estación" está pendiente
    -- hoy usa siempre estacion.umbral_alerta. Implementarlo bien
    requeriría otro snapshot inmutable por evento (igual que
    tiempo_ideal_seg), y hoy ningún dato real ejercita este camino
    (no hay estaciones tipo "calidad" en los tenants existentes).
    """
    try:
        validar_planta(context)
    except ValueError:
        # Estado legítimo de "sin datos" (no hay planta seleccionada aún),
        # no es un error interno -- se mantiene la tarjeta vacía.
        return OeeGeneralCard(
            disponibilidad_pct=0.0, rendimiento_pct=0.0, calidad_pct=None, oee_general_pct=0.0,
            total_unidades=0, unidades_con_retrabajo=0, minutos_desvio_calidad=0.0
        )

    try:
        m = _calcular_metricas_oee(db, context, fecha_desde, fecha_hasta, linea_id, turno_id, orden_fk)
        if m is None:
            return OeeGeneralCard(
                disponibilidad_pct=0.0, rendimiento_pct=0.0, calidad_pct=None, oee_general_pct=0.0,
                total_unidades=0, unidades_con_retrabajo=0, minutos_desvio_calidad=0.0
            )

        return OeeGeneralCard(
            disponibilidad_pct=round(m["disponibilidad"] * 100, 1),
            rendimiento_pct=round(m["rendimiento"] * 100, 1),
            calidad_pct=m["calidad_pct"],
            oee_general_pct=round(m["oee_general"] * 100, 1),
            total_unidades=m["total_unidades"],
            unidades_con_retrabajo=m["total_rechazadas"],
            minutos_desvio_calidad=0.0
        )
    except HTTPException:
        raise
    except Exception as e:
        # Fase E2: un error interno NUNCA debe verse igual que "sin datos".
        # Antes esto devolvía una tarjeta en cero, indistinguible de un
        # tenant sin producción -- podía esconder fallas reales del negocio.
        logger.error(f"Error interno calculando OEE general: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="No se pudo calcular el OEE general en este momento.")


def _calcular_metricas_oee(
    db: Session, context: TenantContext,
    fecha_desde: Optional[date], fecha_hasta: Optional[date],
    linea_id: Optional[uuid.UUID], turno_id: Optional[uuid.UUID],
    orden_fk: Optional[str] = None,
) -> Optional[dict]:
    """Núcleo del motor OEE (Fase E2), extraído para reusarse en oee-general
    y oee-cascada (Fase I) -- una sola fuente de verdad para las fórmulas,
    nunca se recalculan por separado en cada endpoint. Devuelve None si no
    hay eventos en el período (equivalente al "panel vacío" de antes).

    Fase O (auditoría de producción del front #2): antes usaba
    `obtener_rango_dia(fecha_desde)` solo, así que `fecha_hasta` quedaba
    silenciosamente ignorado -- pedir un rango de 30 días devolvía nada más
    que el primer día. Ahora el rango realmente cubre [fecha_desde,
    fecha_hasta]. También suma el filtro opcional por orden (`orden_fk`,
    el "Plan" del dashboard)."""
    inicio, _ = obtener_rango_dia(fecha_desde)
    _, fin = obtener_rango_dia(fecha_hasta or fecha_desde)

    query = (
        select(LiteEventoProduccion, Estacion, Linea)
        .join(Estacion, LiteEventoProduccion.id_estacion == cast(Estacion.id, String))
        .join(Linea, Estacion.linea_id == Linea.id)
        .where(
            LiteEventoProduccion.tenant_id == context.tenant_id,
            Linea.planta_id == context.sub_tenant_id,
            LiteEventoProduccion.timestamp >= inicio,
            LiteEventoProduccion.timestamp <= fin,
            LiteEventoProduccion.incluido_oee == True,  # noqa: E712
        )
    )
    if linea_id: query = query.where(Linea.id == linea_id)
    if orden_fk: query = query.where(LiteEventoProduccion.orden_fk == orden_fk)
    eventos = db.exec(query).all()

    if not eventos:
        return None

    total_unidades = sum(e.unidades_procesadas for e, _, _ in eventos)
    dias_consulta = max(1, (fin.date() - inicio.date()).days + 1)

    q_turnos = select(Turno).join(Linea, Turno.linea_id == Linea.id).where(
        Turno.tenant_id == context.tenant_id,
        Linea.planta_id == context.sub_tenant_id
    )
    if linea_id: q_turnos = q_turnos.where(Turno.linea_id == linea_id)
    if turno_id: q_turnos = q_turnos.where(Turno.id == turno_id)
    turnos = db.exec(q_turnos).all()

    tiempo_planificado_seg = 0
    for t in turnos:
        inicio_dt = datetime.combine(date.today(), t.hora_inicio)
        fin_dt = datetime.combine(date.today(), t.hora_fin)
        if fin_dt < inicio_dt: fin_dt += timedelta(days=1)

        duracion_turno_seg = (fin_dt - inicio_dt).total_seconds()
        duracion_neta_seg = duracion_turno_seg - (t.descanso_minutos * 60)
        tiempo_planificado_seg += (duracion_neta_seg * dias_consulta)

    if tiempo_planificado_seg == 0:
        tiempo_planificado_seg = 28800 * dias_consulta

    q_paradas = (
        select(ParadaDetectada, MotivoParada)
        .outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id)
        .join(Estacion, ParadaDetectada.estacion_fk == Estacion.id)
        .join(Linea, Estacion.linea_id == Linea.id)
        .where(
            ParadaDetectada.tenant_id == context.tenant_id,
            Linea.planta_id == context.sub_tenant_id,
            ParadaDetectada.inicio >= inicio,
            ParadaDetectada.inicio <= fin
        )
    )
    if linea_id: q_paradas = q_paradas.where(Estacion.linea_id == linea_id)
    paradas_db = db.exec(q_paradas).all()

    t_paradas_no_planificadas = sum(p.duracion_segundos or 0 for p, m in paradas_db if not m or m.tipo_parada == TipoParada.NO_PLANIFICADA)
    t_paradas_planificadas = sum(p.duracion_segundos or 0 for p, m in paradas_db if m and m.tipo_parada == TipoParada.PLANIFICADA)

    tiempo_planificado_neto = max(1, tiempo_planificado_seg - t_paradas_planificadas)
    tiempo_operativo_seg = max(1, tiempo_planificado_neto - t_paradas_no_planificadas)

    disponibilidad = min(tiempo_operativo_seg / tiempo_planificado_neto, 1.0)

    # RENDIMIENTO (Fase E2): tiempo_ideal_seg es snapshot por evento
    # (umbral SKU si había uno activo), multiplicado por unidades_procesadas.
    tiempo_ideal_total = sum(e.tiempo_ideal_seg * e.unidades_procesadas for e, _, _ in eventos)
    rendimiento = min((tiempo_ideal_total / tiempo_operativo_seg) if tiempo_operativo_seg > 0 else 0.0, 1.0)

    # CALIDAD (Fase E2): combina POR_RECHAZO y POR_TIEMPO según
    # Linea.metodo_calidad de cada evento. Fallback explícito a N/A.
    unidades_buenas_total = 0
    unidades_calidad_total = 0
    total_rechazadas = sum(e.unidades_rechazadas for e, _, _ in eventos)

    for evento, estacion, linea in eventos:
        if linea.metodo_calidad == "por_rechazo":
            unidades_calidad_total += evento.unidades_procesadas
            unidades_buenas_total += (evento.unidades_procesadas - evento.unidades_rechazadas)
        elif linea.metodo_calidad == "por_tiempo" and estacion.tipo.lower() == "calidad":
            # Limitación documentada en obtener_oee_general: umbral de estación, no de SKU.
            umbral_calidad_aplicable = estacion.umbral_alerta
            unidades_calidad_total += evento.unidades_procesadas
            if (evento.delta_t_segundos or 0) <= umbral_calidad_aplicable:
                unidades_buenas_total += evento.unidades_procesadas

    if unidades_calidad_total > 0:
        calidad = unidades_buenas_total / unidades_calidad_total
        calidad_pct = round(calidad * 100, 1)
    else:
        calidad = None
        calidad_pct = None

    oee_factores = [disponibilidad, rendimiento] + ([calidad] if calidad is not None else [])
    oee_general = 1.0
    for f in oee_factores:
        oee_general *= f

    return {
        "disponibilidad": disponibilidad,
        "rendimiento": rendimiento,
        "calidad": calidad,
        "calidad_pct": calidad_pct,
        "oee_general": oee_general,
        "total_unidades": total_unidades,
        "total_rechazadas": total_rechazadas,
        "dias_consulta": dias_consulta,
        "tiempo_calendario_seg": 86400 * dias_consulta,
        "tiempo_planificado_seg": tiempo_planificado_seg,
        "tiempo_planificado_neto_seg": tiempo_planificado_neto,
        "tiempo_operativo_seg": tiempo_operativo_seg,
    }


@router.get("/analytics/reporte-operarios/", response_model=list[ReporteOperarioSpringwall])
def obtener_reporte_springwall(
    skip: int = 0, limit: int = 1000, fecha: date = None,
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, turno_id: Optional[uuid.UUID] = None,
    orden_fk: Optional[str] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Fase O (auditoría de producción del front #2/#3): se agregan
    fecha_desde/fecha_hasta/linea_id/turno_id/orden_fk para que los filtros
    del dashboard tengan efecto real acá. `fecha` (día único) se mantiene
    por compatibilidad -- si viene, gana sobre fecha_desde/fecha_hasta.
    `turno_id` filtra los eventos por franja horaria del turno, no por un
    campo directo en el evento (LiteEventoProduccion no guarda turno_fk)."""
    try:
        validar_planta(context)
        if fecha is not None:
            inicio_dia, fin_dia = obtener_rango_dia(fecha)
        else:
            inicio_dia, _ = obtener_rango_dia(fecha_desde)
            _, fin_dia = obtener_rango_dia(fecha_hasta or fecha_desde)

        query = (
            select(LiteEventoProduccion, Estacion, Operario)
            .join(Estacion, LiteEventoProduccion.id_estacion == cast(Estacion.id, String))
            .join(Linea, Estacion.linea_id == Linea.id)
            # Safe outer join por si operario_fk no existe o es NULL
            .outerjoin(Operario, getattr(LiteEventoProduccion, "operario_fk", None) == Operario.id)
            .where(
                LiteEventoProduccion.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                LiteEventoProduccion.timestamp >= inicio_dia,
                LiteEventoProduccion.timestamp <= fin_dia
            )
        )
        if linea_id: query = query.where(Linea.id == linea_id)
        if orden_fk: query = query.where(LiteEventoProduccion.orden_fk == orden_fk)
        query = query.offset(skip).limit(limit)
        eventos = db.exec(query).all()

        if turno_id:
            turno = db.exec(select(Turno).where(Turno.id == turno_id, Turno.tenant_id == context.tenant_id)).first()
            if turno:
                def _en_turno(ts, hi, hf):
                    hora = ts.time()
                    return (hi <= hora <= hf) if hi <= hf else (hora >= hi or hora <= hf)
                eventos = [
                    (e, est, op) for e, est, op in eventos
                    if _en_turno(e.timestamp, turno.hora_inicio, turno.hora_fin)
                ]

        data_agrupada = {}
        for evento, estacion, operario in eventos:
            nombre_op = operario.nombre_completo if operario else "Sin Asignar"
            clave = (nombre_op, estacion.nombre)
            
            if clave not in data_agrupada:
                data_agrupada[clave] = {
                    "cantidad_real": 0, "tiempo_invertido": 0, "umbral_optimo": estacion.umbral_optimo
                }
                
            grupo = data_agrupada[clave]
            grupo["cantidad_real"] += evento.unidades_procesadas 
            
            if evento.delta_t_segundos and evento.delta_t_segundos > 0:
                grupo["tiempo_invertido"] += evento.delta_t_segundos

        reporte_final = []
        for (nombre_op, nombre_est), metricas in data_agrupada.items():
            esperada = metricas["cantidad_real"]
            if metricas["umbral_optimo"] > 0:
                esperada = max(1, int(metricas["tiempo_invertido"] / metricas["umbral_optimo"]))
                
            diferencia = ((metricas["cantidad_real"] - esperada) / esperada) * 100
            
            reporte_final.append(
                ReporteOperarioSpringwall(
                    operario_nombre=nombre_op, estacion_nombre=nombre_est,
                    cantidad_real=metricas["cantidad_real"], cantidad_esperada=esperada,
                    diferencia_pct=round(diferencia, 1)
                )
            )
            
        reporte_final.sort(key=lambda x: x.diferencia_pct)
        return reporte_final
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error Reporte Operario: {e}")
        return []


@router.get("/analytics/pareto-paradas/", response_model=list[ParetoParadas])
def obtener_pareto_paradas(
    skip: int = 0, limit: int = 1000, fecha: date = None,
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Fase O (auditoría de producción del front #2): fecha_desde/
    fecha_hasta/linea_id para que los filtros del dashboard tengan efecto
    real. Sin filtro por orden -- ParadaDetectada no tiene relación con
    OrdenProduccion, el "Plan" no aplica a este reporte."""
    try:
        validar_planta(context)
        if fecha is not None:
            inicio_dia, fin_dia = obtener_rango_dia(fecha)
        else:
            inicio_dia, _ = obtener_rango_dia(fecha_desde)
            _, fin_dia = obtener_rango_dia(fecha_hasta or fecha_desde)

        query = (
            select(ParadaDetectada, MotivoParada)
            .join(Estacion, ParadaDetectada.estacion_fk == Estacion.id)
            .join(Linea, Estacion.linea_id == Linea.id)
            .outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id)
            .where(
                ParadaDetectada.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                ParadaDetectada.inicio >= inicio_dia,
                ParadaDetectada.inicio <= fin_dia
            )
        )
        if linea_id: query = query.where(Linea.id == linea_id)
        query = query.offset(skip).limit(limit)
        paradas = db.exec(query).all()

        agrupado = {}
        for parada, motivo in paradas:
            nombre_motivo = motivo.nombre if motivo else "Sin Clasificar (Pendiente)"
            tipo_motivo = str(motivo.tipo_parada).split(".")[-1].upper() if motivo else "DESCONOCIDO"
            
            if nombre_motivo not in agrupado:
                agrupado[nombre_motivo] = {"tipo": tipo_motivo, "frecuencia": 0, "segundos": 0}
                
            agrupado[nombre_motivo]["frecuencia"] += 1
            agrupado[nombre_motivo]["segundos"] += parada.duracion_segundos or 0

        reporte = [
            ParetoParadas(
                motivo=k, tipo=v["tipo"], frecuencia=v["frecuencia"],
                minutos_totales=round(v["segundos"] / 60, 1)
            )
            for k, v in agrupado.items()
        ]
        
        reporte.sort(key=lambda x: x.minutos_totales, reverse=True)
        return reporte
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error Pareto: {e}")
        return []


@router.get("/analytics/cuellos-botella/", response_model=list[CuelloBotella])
def obtener_cuellos_botella(
    skip: int = 0, limit: int = 1000, fecha: date = None,
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Fase O (auditoría de producción del front #2): fecha_desde/
    fecha_hasta/linea_id para que los filtros del dashboard tengan efecto
    real."""
    try:
        validar_planta(context)
        if fecha is not None:
            inicio_dia, fin_dia = obtener_rango_dia(fecha)
        else:
            inicio_dia, _ = obtener_rango_dia(fecha_desde)
            _, fin_dia = obtener_rango_dia(fecha_hasta or fecha_desde)

        query = (
            select(LiteEventoProduccion, Estacion)
            .join(Estacion, LiteEventoProduccion.id_estacion == cast(Estacion.id, String))
            .join(Linea, Estacion.linea_id == Linea.id)
            .where(
                LiteEventoProduccion.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                LiteEventoProduccion.timestamp >= inicio_dia,
                LiteEventoProduccion.timestamp <= fin_dia,
                LiteEventoProduccion.delta_t_segundos > 0
            )
        )
        if linea_id: query = query.where(Linea.id == linea_id)
        query = query.offset(skip).limit(limit)
        eventos = db.exec(query).all()

        agrupado = {}
        for evento, estacion in eventos:
            if estacion.nombre not in agrupado:
                agrupado[estacion.nombre] = {"esperado": estacion.umbral_optimo, "suma_real": 0, "cantidad": 0}
                
            agrupado[estacion.nombre]["suma_real"] += (evento.delta_t_segundos or 0)
            agrupado[estacion.nombre]["cantidad"] += 1

        res = []
        for n, d in agrupado.items():
            if d["cantidad"] > 0:
                promedio = d["suma_real"] / d["cantidad"]
                desvio = ((promedio - d["esperado"]) / d["esperado"]) * 100 if d["esperado"] else 0
                res.append(CuelloBotella(estacion=n, tiempo_esperado_seg=d["esperado"], tiempo_promedio_real_seg=round(promedio, 1), desvio_pct=round(desvio, 1)))
        return sorted(res, key=lambda x: x.desvio_pct, reverse=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error Cuellos de Botella: {e}")
        return []


@router.get("/analytics/oee-tendencia/", response_model=list[TendenciaOEERow])
def tendencia_oee_diaria(
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, turno_id: Optional[uuid.UUID] = None,
    orden_fk: Optional[str] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Tendencia de OEE día por día, reusando el mismo núcleo de cálculo que
    /analytics/oee-general (Fase O -- hallazgo nuevo, no reportado por la
    auditoría: ANTES este endpoint devolvía una serie 100% inventada,
    `oee=70+i*2` hardcodeado, sin tocar la base en absoluto). Por defecto,
    últimos 7 días hasta hoy. Un día sin eventos da oee/disp/rend=0 (la
    planta no produjo); `cal=None` significa específicamente "sin datos de
    calidad ese día", no 0%."""
    try:
        validar_planta(context)
    except ValueError:
        return []

    try:
        hoy = datetime.now().date()
        hasta = fecha_hasta or hoy
        desde = fecha_desde or (hasta - timedelta(days=6))
        if hasta < desde:
            raise HTTPException(status_code=400, detail="fecha_hasta debe ser mayor o igual a fecha_desde.")
        if (hasta - desde).days > 90:
            raise HTTPException(status_code=400, detail="El rango máximo para la tendencia es de 90 días.")

        filas = []
        dia = desde
        while dia <= hasta:
            m = _calcular_metricas_oee(db, context, dia, dia, linea_id, turno_id, orden_fk)
            if m is None:
                filas.append(TendenciaOEERow(fecha=dia.strftime("%d %b"), oee=0.0, disp=0.0, rend=0.0, cal=None))
            else:
                filas.append(TendenciaOEERow(
                    fecha=dia.strftime("%d %b"),
                    oee=round(m["oee_general"] * 100, 1),
                    disp=round(m["disponibilidad"] * 100, 1),
                    rend=round(m["rendimiento"] * 100, 1),
                    cal=m["calidad_pct"],
                ))
            dia += timedelta(days=1)
        return filas
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en tendencia OEE: {e}")
        return []


@router.get("/analytics/alertas-vivas/", response_model=list[AlertaActiva])
def obtener_alertas_vivas(
    skip: int = 0, limit: int = 1000, 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    try:
        validar_planta(context)
        inicio_dia, fin_dia = obtener_rango_dia()
        alertas = []

        # 1. Paradas Pendientes
        paradas_huerfanas = db.exec(
            select(ParadaDetectada, Estacion)
            .join(Estacion, ParadaDetectada.estacion_fk == Estacion.id)
            .join(Linea, Estacion.linea_id == Linea.id)
            .where(
                ParadaDetectada.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                ParadaDetectada.estado == "pendiente",
                ParadaDetectada.inicio >= inicio_dia,
                ParadaDetectada.inicio <= fin_dia
            )
            .offset(skip)
            .limit(limit)
        ).all()

        for parada, estacion in paradas_huerfanas:
            dur = (parada.duracion_segundos or 0) / 60
            alertas.append(AlertaActiva(
                hora=parada.inicio.strftime("%H:%M:%S"), estacion=estacion.nombre,
                tipo="PARADA_PENDIENTE", mensaje=f"Máquina detenida durante {round(dur, 1)} min. Requiere clasificación."
            ))

        # 2. Eventos Críticos (Lentitud Extrema o Retrabajo) - CAST EXPLÍCITO APLICADO
        eventos_criticos = db.exec(
            select(LiteEventoProduccion, Estacion)
            .join(Estacion, LiteEventoProduccion.id_estacion == cast(Estacion.id, String))
            .join(Linea, Estacion.linea_id == Linea.id)
            .where(
                LiteEventoProduccion.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                LiteEventoProduccion.timestamp >= inicio_dia,
                LiteEventoProduccion.timestamp <= fin_dia,
                (LiteEventoProduccion.estado == "ALERTA") | (LiteEventoProduccion.estado == "RETRABAJO")
            )
            .offset(skip)
            .limit(limit)
        ).all()

        for evento, estacion in eventos_criticos:
            if evento.estado == "RETRABAJO":
                tipo = "RETRABAJO"
                msg = f"Orden OP-{evento.orden_fk or 'N/A'} marcada como defecto de calidad."
            else:
                tipo = "LENTITUD_EXTREMA"
                msg = f"Orden OP-{evento.orden_fk or 'N/A'} superó el umbral de alerta ({round(evento.delta_t_segundos or 0, 1)} seg)."
                
            alertas.append(AlertaActiva(
                hora=evento.timestamp.strftime("%H:%M:%S"), estacion=estacion.nombre,
                tipo=tipo, mensaje=msg
            ))

        alertas.sort(key=lambda x: x.hora, reverse=True)
        return alertas
    except HTTPException:
        raise # Dejamos pasar el 400 del sub_tenant faltante
    except Exception as e:
        logger.error(f"Error fatal en alertas_vivas: {str(e)}")
        # Escudo protector final: Retornamos lista vacía para no romper la UI
        return []


# ==========================================
# ANALÍTICA FALTANTE (Fase I)
# BACKEND_REQUIREMENTS.md §14: oee-cascada, rendimiento-secuencial,
# reporte-produccion, command-center.summary.
# ==========================================
@router.get("/analytics/oee-cascada/", response_model=CascadaOEE)
def obtener_cascada_oee(
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, turno_id: Optional[uuid.UUID] = None,
    orden_fk: Optional[str] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Cascada de pérdidas OEE en 5 etapas (Fase I), reusando el mismo
    núcleo de cálculo que /analytics/oee-general -- nunca se recalculan
    las fórmulas por separado:
    Calendario -> Planificado (turnos) -> Operativo (menos paradas
    planificadas) -> Neto (menos paradas no planificadas = Disponibilidad)
    -> Efectivo (Neto * Rendimiento * Calidad, si hay dato de Calidad)."""
    try:
        validar_planta(context)
    except ValueError:
        return CascadaOEE(
            tiempo_calendario_min=0.0, tiempo_planificado_min=0.0, tiempo_operativo_min=0.0,
            tiempo_neto_min=0.0, tiempo_efectivo_min=0.0,
        )

    try:
        m = _calcular_metricas_oee(db, context, fecha_desde, fecha_hasta, linea_id, turno_id, orden_fk)
        if m is None:
            return CascadaOEE(
                tiempo_calendario_min=0.0, tiempo_planificado_min=0.0, tiempo_operativo_min=0.0,
                tiempo_neto_min=0.0, tiempo_efectivo_min=0.0,
            )

        tiempo_efectivo_seg = m["tiempo_operativo_seg"] * m["rendimiento"] * (m["calidad"] if m["calidad"] is not None else 1.0)

        return CascadaOEE(
            tiempo_calendario_min=round(m["tiempo_calendario_seg"] / 60, 1),
            tiempo_planificado_min=round(m["tiempo_planificado_seg"] / 60, 1),
            tiempo_operativo_min=round(m["tiempo_planificado_neto_seg"] / 60, 1),
            tiempo_neto_min=round(m["tiempo_operativo_seg"] / 60, 1),
            tiempo_efectivo_min=round(tiempo_efectivo_seg / 60, 1),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error interno calculando cascada OEE: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="No se pudo calcular la cascada de OEE en este momento.")


@router.get("/analytics/rendimiento-secuencial/", response_model=list[RendimientoSecuencialRow])
def obtener_rendimiento_secuencial(
    fecha: date = None, linea_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Tiempo de ciclo promedio por estación, ordenado por su posición
    física en la línea -- para detectar en qué punto de la secuencia se
    frena el flujo."""
    try:
        validar_planta(context)
        inicio_dia, fin_dia = obtener_rango_dia(fecha)

        query = (
            select(LiteEventoProduccion, Estacion)
            .join(Estacion, LiteEventoProduccion.id_estacion == cast(Estacion.id, String))
            .join(Linea, Estacion.linea_id == Linea.id)
            .where(
                LiteEventoProduccion.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                LiteEventoProduccion.timestamp >= inicio_dia,
                LiteEventoProduccion.timestamp <= fin_dia,
                LiteEventoProduccion.delta_t_segundos > 0,
            )
        )
        if linea_id:
            query = query.where(Linea.id == linea_id)
        eventos = db.exec(query).all()

        agrupado = {}
        for evento, estacion in eventos:
            if estacion.id not in agrupado:
                agrupado[estacion.id] = {
                    "nombre": estacion.nombre, "posicion": estacion.posicion_linea,
                    "objetivo": estacion.umbral_optimo, "suma": 0.0, "cantidad": 0,
                }
            agrupado[estacion.id]["suma"] += evento.delta_t_segundos or 0
            agrupado[estacion.id]["cantidad"] += 1

        resultado = [
            RendimientoSecuencialRow(
                estacion=d["nombre"], posicion_linea=d["posicion"],
                tiempo_ciclo_prom=round(d["suma"] / d["cantidad"], 1) if d["cantidad"] else 0.0,
                objetivo=d["objetivo"],
            )
            for d in agrupado.values()
        ]
        resultado.sort(key=lambda r: r.posicion_linea)
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en rendimiento secuencial: {e}")
        return []


@router.get("/analytics/reporte-produccion/", response_model=list[ReporteProduccionRow])
def obtener_reporte_produccion(
    fecha_desde: date, fecha_hasta: date,
    linea_id: Optional[uuid.UUID] = None, orden_fk: Optional[str] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Filas planas por estación y día, para exportar a Excel (Fase I).
    Misma agrupación que /reportes/dashboard pero con rango de fechas en
    vez de sólo el día de hoy. Fase O: se agrega linea_id/orden_fk para
    que los filtros del dashboard tengan efecto real acá también."""
    try:
        validar_planta(context)
        if fecha_hasta < fecha_desde:
            raise HTTPException(status_code=400, detail="fecha_hasta debe ser mayor o igual a fecha_desde.")

        inicio = datetime.combine(fecha_desde, time.min)
        fin = datetime.combine(fecha_hasta, time.max)

        query = (
            select(LiteEventoProduccion, Estacion)
            .join(Estacion, LiteEventoProduccion.id_estacion == cast(Estacion.id, String))
            .join(Linea, Estacion.linea_id == Linea.id)
            .where(
                LiteEventoProduccion.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                LiteEventoProduccion.timestamp >= inicio,
                LiteEventoProduccion.timestamp <= fin,
            )
        )
        if linea_id: query = query.where(Linea.id == linea_id)
        if orden_fk: query = query.where(LiteEventoProduccion.orden_fk == orden_fk)
        eventos = db.exec(query).all()

        agrupado = {}
        for evento, estacion in eventos:
            clave = (evento.timestamp.date(), estacion.nombre)
            if clave not in agrupado:
                agrupado[clave] = {"total": 0, "optimo": 0, "lento": 0, "alerta": 0, "rechazadas": 0, "suma_tiempos": 0.0, "con_tiempo": 0}
            g = agrupado[clave]
            g["total"] += evento.unidades_procesadas
            g["rechazadas"] += evento.unidades_rechazadas
            if evento.estado == "OPTIMO": g["optimo"] += evento.unidades_procesadas
            elif evento.estado == "LENTO": g["lento"] += evento.unidades_procesadas
            elif evento.estado == "ALERTA": g["alerta"] += evento.unidades_procesadas
            if evento.delta_t_segundos and evento.delta_t_segundos > 0:
                g["suma_tiempos"] += evento.delta_t_segundos
                g["con_tiempo"] += 1

        filas = [
            ReporteProduccionRow(
                fecha=fecha.isoformat(), estacion=nombre,
                total_piezas=g["total"], optimos=g["optimo"], lentos=g["lento"], alertas=g["alerta"],
                rechazadas=g["rechazadas"],
                tiempo_promedio_seg=round(g["suma_tiempos"] / g["con_tiempo"], 2) if g["con_tiempo"] else 0.0,
            )
            for (fecha, nombre), g in agrupado.items()
        ]
        filas.sort(key=lambda f: (f.fecha, f.estacion))
        return filas
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en reporte de producción: {e}")
        return []


@router.get("/analytics/plan-vs-actual/", response_model=list[PlanVsActualRow])
def obtener_plan_vs_actual(
    fecha_desde: date, fecha_hasta: date,
    linea_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Cruza el plan (OrdenProduccion.cantidad_esperada) contra la
    producción real acumulada de cada orden (suma de
    LiteEventoProduccion.unidades_procesadas agrupada por orden_fk). Antes
    esta vista era mock siempre en el front -- no existía ningún endpoint
    que hiciera este cruce (Fase N, auditoría de producción #7).

    cantidad_producida es acumulado a la fecha de consulta, no acotado a
    fecha_desde/fecha_hasta: ese rango sólo filtra qué órdenes del plan se
    muestran, no los eventos de producción que las completan (una orden
    puede seguir sumando piezas después de su plan_fecha)."""
    try:
        validar_planta(context)
        if fecha_hasta < fecha_desde:
            raise HTTPException(status_code=400, detail="fecha_hasta debe ser mayor o igual a fecha_desde.")

        query = (
            select(OrdenProduccion, Linea)
            .join(Linea, OrdenProduccion.linea_id == Linea.id)
            .where(
                OrdenProduccion.tenant_id == context.tenant_id,
                OrdenProduccion.activo == True,  # noqa: E712
                Linea.planta_id == context.sub_tenant_id,
                OrdenProduccion.plan_fecha >= fecha_desde.isoformat(),
                OrdenProduccion.plan_fecha <= fecha_hasta.isoformat(),
            )
        )
        if linea_id:
            query = query.where(Linea.id == linea_id)
        ordenes = db.exec(query).all()

        if not ordenes:
            return []

        ids_orden = [orden.id_orden for orden, _ in ordenes]
        eventos = db.exec(
            select(LiteEventoProduccion.orden_fk, LiteEventoProduccion.unidades_procesadas)
            .where(
                LiteEventoProduccion.tenant_id == context.tenant_id,
                LiteEventoProduccion.orden_fk.in_(ids_orden),
            )
        ).all()
        producido_por_orden: dict = {}
        for orden_fk, unidades in eventos:
            producido_por_orden[orden_fk] = producido_por_orden.get(orden_fk, 0) + (unidades or 0)

        filas = [
            PlanVsActualRow(
                id_orden=orden.id_orden, sku_fk=orden.sku_fk,
                linea_id=linea.id, linea_nombre=linea.nombre,
                plan_fecha=orden.plan_fecha, estado=str(orden.estado.value if hasattr(orden.estado, "value") else orden.estado),
                cantidad_esperada=orden.cantidad_esperada,
                cantidad_producida=producido_por_orden.get(orden.id_orden, 0),
                cumplimiento_pct=(
                    round(producido_por_orden.get(orden.id_orden, 0) / orden.cantidad_esperada * 100, 1)
                    if orden.cantidad_esperada else None
                ),
            )
            for orden, linea in ordenes
        ]
        filas.sort(key=lambda f: (f.plan_fecha or "", f.id_orden))
        return filas
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en plan vs actual: {e}")
        return []


# Sin eventos hace más de esto, la estación se considera "sin_datos" (no
# "offline": no sabemos si es un corte real o simplemente no hay scans en
# esta estación ahora mismo -- ver nota de ParadaDetectada.PENDIENTE, que
# sí es la señal fuerte de corte real).
UMBRAL_SIN_DATOS_SEG = 15 * 60


@router.get("/analytics/linea-en-vivo/", response_model=LineaEnVivoResumen)
def obtener_linea_en_vivo(
    linea_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """Estado en vivo de una línea para la portada de TYMEO (Fase O,
    auditoría de producción del front #1 -- antes esa pantalla era 100%
    mock: turno/orden/operarios de Springwall fijos, sin tocar el backend).

    Turno actual: se resuelve por horario contra los Turnos configurados
    para la línea, no por AsignacionSupervisor ni nada externo -- si
    ninguno matchea la hora actual, turno_actual queda None (no se
    inventa un turno).

    Orden activa: la OrdenProduccion más reciente en estado EN_PROGRESO
    para la línea. Deliberadamente NO se usa Estacion.orden_activa_fk /
    sku_activo_fk -- esos campos existen en el modelo pero ningún flujo
    real los escribe todavía (confirmado: sólo se leen en scans.py, nunca
    se asignan), así que siempre estarían en NULL para un tenant real.

    Estado por estación: derivado del último LiteEventoProduccion (activa
    si hubo un evento hace menos de UMBRAL_SIN_DATOS_SEG) y de si hay una
    ParadaDetectada PENDIENTE abierta ahora mismo (gana sobre "activa").
    El operario asignado sale de AsignacionTurno para hoy + el turno
    resuelto arriba."""
    try:
        linea = db.exec(select(Linea).where(Linea.id == linea_id, Linea.tenant_id == context.tenant_id)).first()
        if not linea:
            raise HTTPException(status_code=404, detail="Línea no encontrada.")
        if context.sub_tenant_id and str(linea.planta_id) != str(context.sub_tenant_id):
            raise HTTPException(status_code=404, detail="Línea no encontrada en la planta activa.")

        ahora_time = datetime.now().time()
        turnos = db.exec(
            select(Turno).where(
                Turno.tenant_id == context.tenant_id,
                Turno.linea_id == linea_id,
                Turno.activo == True,  # noqa: E712
            )
        ).all()
        turno_actual = None
        for t in turnos:
            if t.hora_inicio <= t.hora_fin:
                en_turno = t.hora_inicio <= ahora_time <= t.hora_fin
            else:  # turno que cruza medianoche
                en_turno = ahora_time >= t.hora_inicio or ahora_time <= t.hora_fin
            if en_turno:
                turno_actual = t
                break

        orden_activa = db.exec(
            select(OrdenProduccion)
            .where(
                OrdenProduccion.tenant_id == context.tenant_id,
                OrdenProduccion.linea_id == linea_id,
                OrdenProduccion.estado == EstadoOrden.EN_PROGRESO,
                OrdenProduccion.activo == True,  # noqa: E712
            )
            .order_by(OrdenProduccion.id_orden.desc())
        ).first()

        estaciones = db.exec(
            select(Estacion)
            .where(
                Estacion.tenant_id == context.tenant_id,
                Estacion.linea_id == linea_id,
                Estacion.activa == True,  # noqa: E712
            )
            .order_by(Estacion.posicion_linea)
        ).all()

        operario_por_estacion = {}
        if turno_actual and estaciones:
            asignaciones = db.exec(
                select(AsignacionTurno).where(
                    AsignacionTurno.tenant_id == context.tenant_id,
                    AsignacionTurno.fecha == date.today(),
                    AsignacionTurno.turno_fk == turno_actual.id,
                    AsignacionTurno.estacion_fk.in_([e.id for e in estaciones]),
                )
            ).all()
            operario_por_estacion = {a.estacion_fk: a.operario_fk for a in asignaciones}

        operarios_por_id = {}
        if operario_por_estacion:
            ops = db.exec(
                select(Operario).where(Operario.id.in_(list(operario_por_estacion.values())))
            ).all()
            operarios_por_id = {o.id: o.nombre_completo for o in ops}

        ahora_dt = datetime.utcnow()
        filas_estaciones = []
        for est in estaciones:
            ultimo = db.exec(
                select(LiteEventoProduccion)
                .where(LiteEventoProduccion.id_estacion == str(est.id))
                .order_by(LiteEventoProduccion.timestamp.desc())
            ).first()

            if ultimo:
                segundos_desde_ultimo = (ahora_dt - ultimo.timestamp).total_seconds()
                estado = "activa" if segundos_desde_ultimo <= UMBRAL_SIN_DATOS_SEG else "sin_datos"
                ultimo_ts = ultimo.timestamp.isoformat()
            else:
                estado = "sin_datos"
                ultimo_ts = None

            parada_abierta = db.exec(
                select(ParadaDetectada).where(
                    ParadaDetectada.tenant_id == context.tenant_id,
                    ParadaDetectada.estacion_fk == est.id,
                    ParadaDetectada.estado == EstadoParada.PENDIENTE,
                )
            ).first()
            if parada_abierta:
                estado = "parada"

            operario_id = operario_por_estacion.get(est.id)
            filas_estaciones.append(LineaEnVivoEstacion(
                estacion_fk=est.id,
                estacion_nombre=est.nombre,
                posicion_linea=est.posicion_linea,
                estado=estado,
                operario_id=operario_id,
                operario_nombre=operarios_por_id.get(operario_id) if operario_id else None,
                ultimo_evento=ultimo_ts,
            ))

        return LineaEnVivoResumen(
            linea_id=linea.id,
            linea_nombre=linea.nombre,
            turno_actual=turno_actual.nombre if turno_actual else None,
            orden_activa=orden_activa.id_orden if orden_activa else None,
            orden_sku=orden_activa.sku_fk if orden_activa else None,
            estaciones=filas_estaciones,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en línea en vivo: {e}")
        return LineaEnVivoResumen()


@router.get("/analytics/rendimiento-operarios/", response_model=list[RendimientoOperarioRow])
def obtener_rendimiento_operarios(
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, operario_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """Reporte histórico por operario (Fase O, auditoría de producción del
    front #4 -- antes el front llamaba a esta URL pero el endpoint no
    existía en absoluto). Por defecto, últimos 7 días.

    La resolución operario→evento es la misma que ya usa /eventos/live y
    /analytics/reporte-operarios: vía AsignacionTurno por (estación,
    fecha) del evento, no un campo directo en LiteEventoProduccion (no
    existe). Eventos sin asignación resuelta se excluyen -- no se le
    puede atribuir producción a "nadie" en un reporte por operario."""
    try:
        validar_planta(context)
    except ValueError:
        return []

    try:
        hoy = datetime.now().date()
        hasta = fecha_hasta or hoy
        desde = fecha_desde or (hasta - timedelta(days=6))
        if hasta < desde:
            raise HTTPException(status_code=400, detail="fecha_hasta debe ser mayor o igual a fecha_desde.")

        inicio = datetime.combine(desde, time.min)
        fin = datetime.combine(hasta, time.max)

        query = (
            select(LiteEventoProduccion, Estacion)
            .join(Estacion, LiteEventoProduccion.id_estacion == cast(Estacion.id, String))
            .join(Linea, Estacion.linea_id == Linea.id)
            .where(
                LiteEventoProduccion.tenant_id == context.tenant_id,
                Linea.planta_id == context.sub_tenant_id,
                LiteEventoProduccion.timestamp >= inicio,
                LiteEventoProduccion.timestamp <= fin,
            )
        )
        if linea_id: query = query.where(Linea.id == linea_id)
        eventos = db.exec(query).all()

        if not eventos:
            return []

        estacion_ids = {est.id for _, est in eventos}
        fechas = {e.timestamp.date() for e, _ in eventos}
        asignaciones = db.exec(
            select(AsignacionTurno).where(
                AsignacionTurno.tenant_id == context.tenant_id,
                AsignacionTurno.estacion_fk.in_(estacion_ids),
                AsignacionTurno.fecha.in_(fechas),
            )
        ).all()
        operario_por_estacion_fecha = {(a.estacion_fk, a.fecha): a.operario_fk for a in asignaciones}

        agrupado = {}
        for evento, estacion in eventos:
            op_id = operario_por_estacion_fecha.get((estacion.id, evento.timestamp.date()))
            if not op_id:
                continue
            if operario_id and op_id != operario_id:
                continue

            g = agrupado.setdefault(op_id, {
                "estaciones": set(), "unidades": 0, "retrabajos": 0,
                "suma_tiempo": 0.0, "con_tiempo": 0,
                "optimo": 0, "lento": 0, "alerta": 0, "total_eventos": 0,
            })
            g["estaciones"].add(estacion.nombre)
            g["unidades"] += evento.unidades_procesadas
            g["retrabajos"] += evento.unidades_rechazadas
            if evento.delta_t_segundos and evento.delta_t_segundos > 0:
                g["suma_tiempo"] += evento.delta_t_segundos
                g["con_tiempo"] += 1
            g["total_eventos"] += 1
            if evento.estado == "OPTIMO": g["optimo"] += 1
            elif evento.estado == "LENTO": g["lento"] += 1
            elif evento.estado == "ALERTA": g["alerta"] += 1

        if not agrupado:
            return []

        operarios = db.exec(
            select(Operario).where(Operario.id.in_(list(agrupado.keys())))
        ).all()
        operarios_por_id = {o.id: o for o in operarios}

        resultado = []
        for op_id, g in agrupado.items():
            operario = operarios_por_id.get(op_id)
            total = g["total_eventos"] or 1
            resultado.append(RendimientoOperarioRow(
                operario_id=op_id,
                legajo=operario.legajo if operario else "?",
                nombre=operario.nombre_completo if operario else "Operario no encontrado",
                estaciones_operadas=sorted(g["estaciones"]),
                unidades_producidas=g["unidades"],
                retrabajos_generados=g["retrabajos"],
                tiempo_promedio_seg=round(g["suma_tiempo"] / g["con_tiempo"], 1) if g["con_tiempo"] else 0.0,
                distribucion_desempeno={
                    "optimo": round(g["optimo"] / total * 100, 1),
                    "lento": round(g["lento"] / total * 100, 1),
                    "alerta": round(g["alerta"] / total * 100, 1),
                },
                eficiencia_global=round(g["optimo"] / total * 100, 1),
            ))
        resultado.sort(key=lambda r: r.eficiencia_global, reverse=True)
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en rendimiento de operarios: {e}")
        return []


@router.get("/command-center/summary", response_model=CommandCenterSummary)
def obtener_resumen_command_center(
    db: Session = Depends(get_session),
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    """KPIs cross-planta para el home del shell InspectIA OS (Fase I).
    No usa X-Sub-Tenant-Id (es multi-planta por diseño): Gerencia/SuperAdmin/
    Producción ven todas las plantas del tenant; Supervisor/Operario sólo
    las que tengan asignadas vía UsuarioPlanta (mismo criterio RBAC
    geolocalizado del resto de la app).

    Fase M: usaba usuario.tenant_id (el tenant real del usuario logueado)
    en vez de context.tenant_id -- rompía el "Modo Dios" de SuperAdmin
    (?tenant_id=<otro>): al impersonar otra empresa, este endpoint seguía
    mostrando los datos del tenant propio del SuperAdmin en vez del
    impersonado. Bug real encontrado probando Green Mills en dev.

    'infraestructura' se interpreta como estaciones activas/total (no hay
    telemetría de conectividad de hardware más allá de las credenciales
    M2M) -- a confirmar con el frontend cuando conecte este endpoint."""
    query_plantas = select(Planta).where(Planta.tenant_id == context.tenant_id, Planta.activo == True)  # noqa: E712
    if usuario.rol not in (RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.PRODUCCION):
        plantas_asignadas = db.exec(
            select(UsuarioPlanta.planta_id).where(
                UsuarioPlanta.usuario_id == usuario.id, UsuarioPlanta.activo == True,  # noqa: E712
            )
        ).all()
        if not plantas_asignadas:
            return CommandCenterSummary(
                oee_global=None, alertas_activas=0, plantas=[],
                infraestructura=CommandCenterInfraestructura(estaciones_activas=0, estaciones_total=0),
            )
        query_plantas = query_plantas.where(Planta.id.in_(plantas_asignadas))

    plantas_db = db.exec(query_plantas).all()

    plantas_resumen = []
    oees_validos = []
    alertas_activas = 0
    for planta in plantas_db:
        context_planta = TenantContext(tenant_id=context.tenant_id, sub_tenant_id=str(planta.id), is_superadmin=context.is_superadmin)
        m = _calcular_metricas_oee(db, context_planta, None, None, None, None)
        oee_pct = round(m["oee_general"] * 100, 1) if m else None
        if oee_pct is not None:
            oees_validos.append(oee_pct)

        paradas_pendientes = db.exec(
            select(ParadaDetectada)
            .join(Estacion, ParadaDetectada.estacion_fk == Estacion.id)
            .join(Linea, Estacion.linea_id == Linea.id)
            .where(
                ParadaDetectada.tenant_id == context.tenant_id,
                Linea.planta_id == planta.id,
                ParadaDetectada.estado == "pendiente",
            )
        ).all()
        alertas_activas += len(paradas_pendientes)

        plantas_resumen.append(CommandCenterPlanta(
            id=planta.id, nombre=planta.nombre,
            oee=oee_pct, estado="con_datos" if oee_pct is not None else "sin_datos",
        ))

    if plantas_db:
        estaciones_total = db.exec(
            select(Estacion).join(Linea, Estacion.linea_id == Linea.id).where(
                Estacion.tenant_id == context.tenant_id,
                Linea.planta_id.in_([p.id for p in plantas_db]),
            )
        ).all()
    else:
        estaciones_total = []
    estaciones_activas = [e for e in estaciones_total if e.activa]

    return CommandCenterSummary(
        oee_global=round(sum(oees_validos) / len(oees_validos), 1) if oees_validos else None,
        alertas_activas=alertas_activas,
        plantas=plantas_resumen,
        infraestructura=CommandCenterInfraestructura(
            estaciones_activas=len(estaciones_activas), estaciones_total=len(estaciones_total),
        ),
    )
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
    MotivoParada, Operario, Turno, Linea, TipoParada
)
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
    cal: float

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
        inicio, fin = obtener_rango_dia(fecha_desde)

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
        eventos = db.exec(query).all()

        if not eventos:
            return OeeGeneralCard(
                disponibilidad_pct=0.0, rendimiento_pct=0.0, calidad_pct=None, oee_general_pct=0.0,
                total_unidades=0, unidades_con_retrabajo=0, minutos_desvio_calidad=0.0
            )

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
                # Limitación documentada arriba: umbral de estación, no de SKU.
                umbral_calidad_aplicable = estacion.umbral_alerta
                unidades_calidad_total += evento.unidades_procesadas
                if (evento.delta_t_segundos or 0) <= umbral_calidad_aplicable:
                    unidades_buenas_total += evento.unidades_procesadas

        if unidades_calidad_total > 0:
            calidad = unidades_buenas_total / unidades_calidad_total
            calidad_pct = round(calidad * 100, 1)
        else:
            # Sin datos de rechazo ni inspecciones de calidad: N/A, no 100%.
            calidad = None
            calidad_pct = None

        minutos_desvio = 0.0  # Ver limitación de calidad-por-tiempo arriba.

        oee_factores = [disponibilidad, rendimiento] + ([calidad] if calidad is not None else [])
        oee_general = 1.0
        for f in oee_factores:
            oee_general *= f

        return OeeGeneralCard(
            disponibilidad_pct=round(disponibilidad * 100, 1),
            rendimiento_pct=round(rendimiento * 100, 1),
            calidad_pct=calidad_pct,
            oee_general_pct=round(oee_general * 100, 1),
            total_unidades=total_unidades,
            unidades_con_retrabajo=total_rechazadas,
            minutos_desvio_calidad=round(minutos_desvio, 1)
        )
    except HTTPException:
        raise
    except Exception as e:
        # Fase E2: un error interno NUNCA debe verse igual que "sin datos".
        # Antes esto devolvía una tarjeta en cero, indistinguible de un
        # tenant sin producción -- podía esconder fallas reales del negocio.
        logger.error(f"Error interno calculando OEE general: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="No se pudo calcular el OEE general en este momento.")


@router.get("/analytics/reporte-operarios/", response_model=list[ReporteOperarioSpringwall])
def obtener_reporte_springwall(
    skip: int = 0, limit: int = 1000, fecha: date = None, 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    try:
        validar_planta(context)
        inicio_dia, fin_dia = obtener_rango_dia(fecha)
            
        eventos = db.exec(
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
            .offset(skip)
            .limit(limit)
        ).all()

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
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    try:
        validar_planta(context)
        inicio_dia, fin_dia = obtener_rango_dia(fecha)
            
        paradas = db.exec(
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
            .offset(skip)
            .limit(limit)
        ).all()

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
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    try:
        validar_planta(context)
        inicio_dia, fin_dia = obtener_rango_dia(fecha)
            
        eventos = db.exec(
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
            .offset(skip)
            .limit(limit)
        ).all()

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
    linea_id: Optional[uuid.UUID] = None, 
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    try:
        validar_planta(context)
        hoy = datetime.now().date()
        datos = []
        for i in range(5, -1, -1):
            dia = hoy - timedelta(days=i)
            datos.append(TendenciaOEERow(
                fecha=dia.strftime("%d %b"), oee=round(70 + (i*2), 1), 
                disp=round(75 + i, 1), rend=round(80 - i, 1), cal=round(90 + i, 1)
            ))
        return datos
    except HTTPException:
        raise
    except Exception as e:
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
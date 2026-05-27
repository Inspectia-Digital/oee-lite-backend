from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from app.core.database import get_session
# 1. IMPORTAMOS LA NUEVA DEPENDENCIA DE IMPERSONATION
from app.core.auth import obtener_tenant_aislado 
from app.models.domain import Estacion, EventoEscaneo, ParadaDetectada, MotivoParada, Operario, Turno, Linea, TipoParada, UsuarioSaaS
from pydantic import BaseModel
from datetime import datetime, time, date, timedelta
from typing import List, Optional
import uuid

router = APIRouter(tags=["Analytics"])

# --- MOLDES (Schemas) ---
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
    calidad_pct: float
    oee_general_pct: float
    total_unidades: int
    unidades_con_retrabajo: int
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

# --- HELPER FUNCIÓN ---
def obtener_rango_dia(fecha_busqueda: date = None):
    f = fecha_busqueda or datetime.now().date()
    return datetime.combine(f, time.min), datetime.combine(f, time.max)


# ==========================================
# ENDPOINTS BLINDADOS (SOPORTAN "MODO DIOS")
# ==========================================

@router.get("/reportes/dashboard", response_model=list[MetricasEstacion])
def obtener_dashboard_estaciones(
    skip: int = 0, limit: int = 500000, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    inicio_dia, fin_dia = obtener_rango_dia()
    
    resultados = db.exec(
        select(EventoEscaneo, Estacion)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        .where(
            EventoEscaneo.tenant_id == tenant_id, # <-- ACTUALIZADO
            EventoEscaneo.timestamp >= inicio_dia,
            EventoEscaneo.timestamp <= fin_dia
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
        m["total"] += 1
        
        if evento.desempeno == "OPTIMO": m["optimo"] += 1
        elif evento.desempeno == "LENTO": m["lento"] += 1
        elif evento.desempeno == "ALERTA": m["alerta"] += 1
        
        if evento.es_retrabajo:
            m["retrabajo"] += 1
            
        if evento.segundos_proceso and evento.segundos_proceso > 0:
            m["suma_tiempos"] += evento.segundos_proceso
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


@router.get("/analytics/oee-general/", response_model=OeeGeneralCard)
def obtener_oee_general(
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, turno_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    inicio, fin = obtener_rango_dia(fecha_desde)
    
    query = select(EventoEscaneo, Estacion).join(Estacion, EventoEscaneo.estacion_fk == Estacion.id).where(
        EventoEscaneo.tenant_id == tenant_id, # <-- ACTUALIZADO
        EventoEscaneo.timestamp >= inicio,
        EventoEscaneo.timestamp <= fin
    )
    eventos = db.exec(query).all()

    if not eventos:
        return OeeGeneralCard(
            disponibilidad_pct=0.0, rendimiento_pct=0.0, calidad_pct=0.0, oee_general_pct=0.0,
            total_unidades=0, unidades_con_retrabajo=0, minutos_desvio_calidad=0.0
        )

    total_unidades = len(eventos)
    dias_consulta = max(1, (fin.date() - inicio.date()).days + 1)
    
    q_turnos = select(Turno).where(Turno.tenant_id == tenant_id) # <-- ACTUALIZADO
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

    q_paradas = select(ParadaDetectada, MotivoParada).outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id).join(Estacion, ParadaDetectada.estacion_fk == Estacion.id).where(
        ParadaDetectada.tenant_id == tenant_id, # <-- ACTUALIZADO
        ParadaDetectada.inicio >= inicio, 
        ParadaDetectada.inicio <= fin
    )
    if linea_id: q_paradas = q_paradas.where(Estacion.linea_id == linea_id)
    paradas_db = db.exec(q_paradas).all()
    
    t_paradas_no_planificadas = 0
    t_paradas_planificadas = 0
    
    for p, m in paradas_db:
        if p.duracion_segundos:
            if not m or m.tipo_parada == TipoParada.NO_PLANIFICADA:
                t_paradas_no_planificadas += p.duracion_segundos
            else:
                t_paradas_planificadas += p.duracion_segundos
                
    tiempo_planificado_neto = max(1, tiempo_planificado_seg - t_paradas_planificadas)
    tiempo_operativo_seg = max(1, tiempo_planificado_neto - t_paradas_no_planificadas)
    disponibilidad = min(tiempo_operativo_seg / tiempo_planificado_neto, 1.0)

    t_ideal_total = sum(est.umbral_optimo for _, est in eventos)
    rendimiento = min((t_ideal_total / tiempo_operativo_seg) if tiempo_operativo_seg > 0 else 0.0, 1.0)

    eventos_calidad = [(e, est) for e, est in eventos if est.tipo.lower() == "calidad"]
    retrabajos = sum(1 for e, _ in eventos_calidad if e.es_retrabajo)
    
    if len(eventos_calidad) > 0:
        t_ideal_calidad = sum(est.umbral_optimo for _, est in eventos_calidad)
        t_real_calidad = sum((e.segundos_proceso or 0) for e, _ in eventos_calidad)
        calidad = min((t_ideal_calidad / t_real_calidad) if t_real_calidad > 0 else 1.0, 1.0)
        minutos_desvio = max(0, t_real_calidad - t_ideal_calidad) / 60
    else:
        calidad = 1.0
        minutos_desvio = 0.0

    return OeeGeneralCard(
        disponibilidad_pct=round(disponibilidad * 100, 1),
        rendimiento_pct=round(rendimiento * 100, 1),
        calidad_pct=round(calidad * 100, 1),
        oee_general_pct=round((disponibilidad * rendimiento * calidad) * 100, 1),
        total_unidades=total_unidades,
        unidades_con_retrabajo=retrabajos,
        minutos_desvio_calidad=round(minutos_desvio, 1)
    )


@router.get("/analytics/reporte-operarios/", response_model=list[ReporteOperarioSpringwall])
def obtener_reporte_springwall(
    skip: int = 0, limit: int = 500000, fecha: date = None, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    inicio_dia, fin_dia = obtener_rango_dia(fecha)
        
    eventos = db.exec(
        select(EventoEscaneo, Estacion, Operario)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        .outerjoin(Operario, EventoEscaneo.operario_fk == Operario.id)
        .where(
            EventoEscaneo.tenant_id == tenant_id, # <-- ACTUALIZADO
            EventoEscaneo.timestamp >= inicio_dia,
            EventoEscaneo.timestamp <= fin_dia
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
        grupo["cantidad_real"] += 1
        if evento.segundos_proceso and evento.segundos_proceso > 0:
            grupo["tiempo_invertido"] += evento.segundos_proceso

    reporte_final = []
    for (nombre_op, nombre_est), metricas in data_agrupada.items():
        if metricas["umbral_optimo"] > 0:
            esperada = metricas["tiempo_invertido"] / metricas["umbral_optimo"]
            esperada = int(esperada)
        else:
            esperada = metricas["cantidad_real"]
            
        esperada = max(1, esperada) 
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


@router.get("/analytics/pareto-paradas/", response_model=list[ParetoParadas])
def obtener_pareto_paradas(
    skip: int = 0, limit: int = 500000, fecha: date = None, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    inicio_dia, fin_dia = obtener_rango_dia(fecha)
        
    paradas = db.exec(
        select(ParadaDetectada, MotivoParada)
        .outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id)
        .where(
            ParadaDetectada.tenant_id == tenant_id, # <-- ACTUALIZADO
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
        agrupado[nombre_motivo]["segundos"] += parada.duracion_segundos

    reporte = [
        ParetoParadas(
            motivo=k, tipo=v["tipo"], frecuencia=v["frecuencia"],
            minutos_totales=round(v["segundos"] / 60, 1)
        )
        for k, v in agrupado.items()
    ]
    
    reporte.sort(key=lambda x: x.minutos_totales, reverse=True)
    return reporte


@router.get("/analytics/cuellos-botella/", response_model=list[CuelloBotella])
def obtener_cuellos_botella(
    skip: int = 0, limit: int = 500000, fecha: date = None, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    inicio_dia, fin_dia = obtener_rango_dia(fecha)
        
    eventos = db.exec(
        select(EventoEscaneo, Estacion)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        .where(
            EventoEscaneo.tenant_id == tenant_id, # <-- ACTUALIZADO
            EventoEscaneo.timestamp >= inicio_dia,
            EventoEscaneo.timestamp <= fin_dia,
            EventoEscaneo.segundos_proceso > 0 
        )
        .offset(skip)
        .limit(limit)
    ).all()

    agrupado = {}
    for evento, estacion in eventos:
        if estacion.nombre not in agrupado:
            agrupado[estacion.nombre] = {"esperado": estacion.umbral_optimo, "suma_real": 0, "cantidad": 0}
            
        agrupado[estacion.nombre]["suma_real"] += evento.segundos_proceso
        agrupado[estacion.nombre]["cantidad"] += 1

    res = []
    for n, d in agrupado.items():
        promedio = d["suma_real"] / d["cantidad"]
        desvio = ((promedio - d["esperado"]) / d["esperado"]) * 100
        res.append(CuelloBotella(estacion=n, tiempo_esperado_seg=d["esperado"], tiempo_promedio_real_seg=round(promedio, 1), desvio_pct=round(desvio, 1)))
    return sorted(res, key=lambda x: x.desvio_pct, reverse=True)


@router.get("/analytics/oee-tendencia/", response_model=list[TendenciaOEERow])
def tendencia_oee_diaria(
    linea_id: Optional[uuid.UUID] = None, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO (Aunque devuelva mocks ahora, blinda la ruta)
):
    hoy = datetime.now().date()
    datos = []
    for i in range(5, -1, -1):
        dia = hoy - timedelta(days=i)
        datos.append(TendenciaOEERow(
            fecha=dia.strftime("%d %b"), oee=round(70 + (i*2), 1), 
            disp=round(75 + i, 1), rend=round(80 - i, 1), cal=round(90 + i, 1)
        ))

    return datos


@router.get("/analytics/alertas-vivas/", response_model=list[AlertaActiva])
def obtener_alertas_vivas(
    skip: int = 0, limit: int = 50000, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    inicio_dia, fin_dia = obtener_rango_dia()
    alertas = []

    paradas_huerfanas = db.exec(
        select(ParadaDetectada, Estacion)
        .join(Estacion, ParadaDetectada.estacion_fk == Estacion.id)
        .where(
            ParadaDetectada.tenant_id == tenant_id, # <-- ACTUALIZADO
            ParadaDetectada.estado == "pendiente",
            ParadaDetectada.inicio >= inicio_dia,
            ParadaDetectada.inicio <= fin_dia
        )
        .offset(skip)
        .limit(limit)
    ).all()

    for parada, estacion in paradas_huerfanas:
        alertas.append(AlertaActiva(
            hora=parada.inicio.strftime("%H:%M:%S"), estacion=estacion.nombre,
            tipo="PARADA_PENDIENTE", mensaje=f"Máquina detenida durante {round(parada.duracion_segundos/60, 1)} min. Requiere clasificación."
        ))

    eventos_criticos = db.exec(
        select(EventoEscaneo, Estacion)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        .where(
            EventoEscaneo.tenant_id == tenant_id, # <-- ACTUALIZADO
            EventoEscaneo.timestamp >= inicio_dia,
            EventoEscaneo.timestamp <= fin_dia,
            (EventoEscaneo.desempeno == "ALERTA") | (EventoEscaneo.es_retrabajo == True)
        )
        .offset(skip)
        .limit(limit)
    ).all()

    for evento, estacion in eventos_criticos:
        if evento.es_retrabajo:
            tipo = "RETRABAJO"
            msg = f"Colchón OP-{evento.orden_fk} marcado como defecto de calidad."
        else:
            tipo = "LENTITUD_EXTREMA"
            msg = f"Colchón OP-{evento.orden_fk} superó el umbral de alerta ({evento.segundos_proceso} seg)."
            
        alertas.append(AlertaActiva(
            hora=evento.timestamp.strftime("%H:%M:%S"), estacion=estacion.nombre,
            tipo=tipo, mensaje=msg
        ))

    alertas.sort(key=lambda x: x.hora, reverse=True)
    return alertas
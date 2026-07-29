import re
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from sqlmodel import Session, select
from datetime import datetime, timezone
import uuid

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_edge, TenantContext
from app.models.domain import (
    Estacion, Linea, MaestroSKU, Tenant, 
    LiteEventoProduccion, ParadaDetectada, EstadoParada,
    ModoAsignacionOperariosEstacion
)

router = APIRouter(prefix="/api/lite", tags=["Ingesta de Datos (Terminales y PLC)"])

class ScanRequest(BaseModel):
    id_estacion: uuid.UUID = Field(..., description="UUID de la estación o máquina")
    codigo_pieza: Optional[str] = Field(None, description="Código de barras escaneado o nulo si es PLC")
    timestamp: Optional[datetime] = None

@router.get("/estaciones/{estacion_id}/validar", response_model=Dict[str, Any])
def validar_estacion_terminal(
    estacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_edge)
):
    """(Bootstrap) Kiosko solicita configuración de la estación y herencia de línea."""
    estacion = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada o no autorizada")
        
    linea = db.exec(select(Linea).where(Linea.id == estacion.linea_id)).first()

    modo_asignacion = estacion.modo_asignacion_operarios
    if modo_asignacion == ModoAsignacionOperariosEstacion.HEREDAR and linea:
        modo_asignacion = linea.modo_asignacion_operarios

    return {
        "estacion_id": str(estacion.id),
        "estacion_nombre": estacion.nombre,
        "tipo_produccion": linea.tipo_produccion if linea else "discreta",
        "modo_asignacion_operarios": modo_asignacion
    }

@router.post("/scans", status_code=status.HTTP_201_CREATED)
def registrar_escaneo_rapido(
    scan: ScanRequest,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_edge)
):
    """
    Motor DTR Universal: Procesa pings en < 50ms[cite: 14].
    Calcula Rendimiento, detecta micro-paradas y soporta lotes (Green Mills).
    """
    estacion = db.exec(select(Estacion).where(Estacion.id == scan.id_estacion, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación inválida")
        
    linea = db.exec(select(Linea).where(Linea.id == estacion.linea_id)).first()
    tenant_config = db.get(Tenant, context.tenant_id)

    # 1. RESOLUCIÓN DE TRAZABILIDAD (Regex Dinámico vs PLC Ciego)[cite: 14]
    orden_final = estacion.orden_activa_fk
    sku_final = estacion.sku_activo_fk

    if scan.codigo_pieza and tenant_config and tenant_config.regex_parser_orden:
        try:
            match = re.search(tenant_config.regex_parser_orden, scan.codigo_pieza.strip())
            if match:
                orden_final = match.group(1)
        except Exception:
            pass 

    # 2. FACTOR DE LOTE (Green Mills)
    unidades_a_sumar = 1
    t_optimo, t_lento, t_alerta = estacion.umbral_optimo, estacion.umbral_lento, estacion.umbral_alerta

    if sku_final:
        sku = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == sku_final, MaestroSKU.tenant_id == context.tenant_id)).first()
        if sku:
            if linea and linea.tipo_produccion == "por_lotes":
                unidades_a_sumar = sku.unidades_por_ciclo
            
            # Tolerancias dinámicas[cite: 13]
            t_optimo = sku.tiempo_ciclo_teorico
            t_lento = t_optimo * (tenant_config.tolerancia_lento_pct if tenant_config else 1.15)
            t_alerta = t_optimo * (tenant_config.tolerancia_alerta_pct if tenant_config else 1.25)

    # 3. CÁLCULO OEE DEDUCTIVO (Time-Based DTR)
    evento_timestamp = scan.timestamp or datetime.now(timezone.utc).replace(tzinfo=None)
    delta_t_segundos = 0.0
    desempeno = "OPTIMO"

    ultimo_evento = db.exec(
        select(LiteEventoProduccion)
        .where(LiteEventoProduccion.id_estacion == str(estacion.id))
        .order_by(LiteEventoProduccion.timestamp.desc())
    ).first()

    if ultimo_evento:
        delta_t_segundos = (evento_timestamp - ultimo_evento.timestamp).total_seconds()
        
        if delta_t_segundos > t_alerta:
            desempeno = "ALERTA"
            # Disparar Parada Automática[cite: 13]
            nueva_parada = ParadaDetectada(
                tenant_id=context.tenant_id,
                estacion_fk=estacion.id,
                inicio=ultimo_evento.timestamp,
                fin=evento_timestamp,
                duracion_segundos=delta_t_segundos,
                estado=EstadoParada.PENDIENTE
            )
            db.add(nueva_parada)
            # Cap de Rendimiento: Limitamos delta_t a t_optimo para no penalizar el OEE de Rendimiento
            delta_t_segundos = t_optimo 
            
        elif delta_t_segundos > t_lento:
            desempeno = "LENTO"

    # 4. Persistencia Edge-Priority[cite: 14]
    nuevo_evento = LiteEventoProduccion(
        tenant_id=context.tenant_id,
        id_estacion=str(estacion.id),
        codigo_pieza=scan.codigo_pieza,
        orden_fk=orden_final,
        cantidad_producida=1, 
        unidades_procesadas=unidades_a_sumar, # Multiplicador guardado inmutable
        timestamp=evento_timestamp,
        delta_t_segundos=delta_t_segundos,
        estado=desempeno
    )
    
    db.add(nuevo_evento)
    db.commit()
    
    return {"status": "ok", "evento_id": nuevo_evento.id, "unidades": unidades_a_sumar, "desempeno": desempeno}
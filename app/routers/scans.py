import re
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
from sqlmodel import Session, select
from datetime import datetime, timezone

from app.core.database import get_session
from app.core.auth import obtener_tenant_aislado
from app.models.domain import LiteEventoProduccion, Estacion, MaestroSKU, Tenant

router = APIRouter(prefix="/api/lite", tags=["Ingesta OEE Lite (Universal)"])

class ScanRequest(BaseModel):
    id_estacion: str = Field(..., description="UUID de la estación o máquina en planta")
    codigo_pieza: Optional[str] = None
    timestamp: Optional[datetime] = None

@router.post("/scans", status_code=status.HTTP_201_CREATED)
def registrar_escaneo_rapido(
    scan: ScanRequest,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    # 1. Traer configuración de la Estación y del Tenant en la misma transacción lógica
    estacion = db.exec(select(Estacion).where(
        Estacion.id == scan.id_estacion, 
        Estacion.tenant_id == tenant_id
    )).first()
    
    if not estacion:
        raise HTTPException(status_code=400, detail="Estación inválida o no pertenece al tenant.")

    tenant_config = db.exec(select(Tenant).where(Tenant.id == tenant_id)).first()

    # Variables universales
    orden_final = None
    cantidad_final = 1
    
    # 2. RESOLUCIÓN DE TRAZABILIDAD HÍBRIDA (Configuration-Driven Regex)
    if scan.codigo_pieza:
        barcode = scan.codigo_pieza.strip()
        orden_final = barcode # Fallback por defecto
        
        if tenant_config and tenant_config.regex_parser_orden:
            try:
                match = re.search(tenant_config.regex_parser_orden, barcode)
                if match:
                    orden_final = match.group(1) # Extrae el fragmento dinámico según la regla del cliente
            except Exception:
                pass 
                
    else:
        # FLUJO B: Trazabilidad Implícita (Ping ciego del PLC / Lotes)
        orden_final = estacion.orden_activa_fk
        if estacion.sku_activo_fk:
            sku = db.exec(select(MaestroSKU).where(
                MaestroSKU.codigo_sku == estacion.sku_activo_fk,
                MaestroSKU.tenant_id == tenant_id
            )).first()
            if sku:
                cantidad_final = sku.unidades_por_ciclo

    # 3. Persistencia de Alta Velocidad (Edge-Priority)
    evento_timestamp = scan.timestamp or datetime.now(timezone.utc)
    
    nuevo_evento = LiteEventoProduccion(
        tenant_id=tenant_id,
        id_estacion=str(estacion.id),
        codigo_pieza=scan.codigo_pieza,
        orden_fk=orden_final,
        cantidad_producida=cantidad_final,
        timestamp=evento_timestamp,
        estado="PENDIENTE"
    )
    
    db.add(nuevo_evento)
    db.commit()
    db.refresh(nuevo_evento)
    
    return {
        "status": "ok", 
        "mensaje": "Escaneo procesado con éxito",
        "evento_id": nuevo_evento.id,
        "orden_asociada": orden_final,
        "cantidad": cantidad_final
    }
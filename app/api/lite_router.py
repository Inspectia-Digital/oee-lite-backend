# app/api/lite_router.py
from fastapi import APIRouter, Depends, status
from sqlmodel import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

from app.core.database import get_session
from app.core.auth import obtener_tenant_aislado 
from app.models.domain import LiteEventoProduccion

router = APIRouter(prefix="/api/lite", tags=["Ingesta OEE Lite"])

# Payload Stateless - El hardware no sabe de qué empresa es, el Token sí.
class ScanPayload(BaseModel):
    id_estacion: str
    codigo_pieza: Optional[str] = None
    timestamp: Optional[datetime] = None

@router.post("/scans", status_code=status.HTTP_201_CREATED)
def registrar_escaneo(
    payload: ScanPayload,
    tenant_id: str = Depends(obtener_tenant_aislado),
    session: Session = Depends(get_session)
):
    """
    Endpoint de ingesta pura orientada a eventos. 
    Aislado por tenant a nivel middleware para evitar Tenant Bleeding.
    """
    # 1. Definir Timestamp (Prioridad al hardware Edge, fallback al reloj del servidor)
    evento_timestamp = payload.timestamp or datetime.utcnow()
    
    # 2. Materializar evento
    nuevo_evento = LiteEventoProduccion(
        tenant_id=tenant_id,
        id_estacion=payload.id_estacion,
        codigo_pieza=payload.codigo_pieza,
        timestamp=evento_timestamp,
        estado="PENDIENTE" # Marcado para ser procesado por el motor matemático
    )
    
    # 3. Persistencia rápida
    session.add(nuevo_evento)
    session.commit()
    session.refresh(nuevo_evento)
    
    return {
        "status": "ok",
        "evento_id": nuevo_evento.id,
        "tenant_id": tenant_id
    }
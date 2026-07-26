from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from datetime import timedelta
import uuid

from app.core.database import get_session
from app.core.auth import obtener_tenant_aislado
from app.models.domain import (
    Estacion, CicloProduccion, EventoEscaneo, 
    ParadaDetectada, EstadoParada, CategoriaParada
)
from pydantic import BaseModel, Field
from datetime import datetime

router = APIRouter(prefix="/webhooks/plc", tags=["IoT & PLC Integrations"])

# --- SCHEMAS DE ENTRADA (Validación estricta del Payload de Node-RED) ---
class PLCDetalles(BaseModel):
    formato_moldes: int = Field(..., description="Formato de la bandeja (4 o 5)")
    panes_producidos: int = Field(..., description="Cantidad de panes por ciclo")
    delta_tiempo_segundos: float = Field(..., description="Segundos desde el último ciclo")

class PLCPayload(BaseModel):
    timestamp: datetime
    id_maquina: str
    evento: str
    detalles: PLCDetalles


# --- ENDPOINT TRADUCTOR ---
@router.post("/produccion", status_code=status.HTTP_201_CREATED)
def registrar_ciclo_plc(
    payload: PLCPayload,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # Autorización B2B M2M
):
    """
    Recibe el evento de basculación (Flip-Flop) desde Node-RED.
    1. Lo guarda crudo para auditoría.
    2. Lo traduce a eventos OEE para alimentar los dashboards en vivo.
    """
    # 1. Mapeo de Hardware a Lógica de Negocio
    estacion = db.exec(
        select(Estacion).where(
            Estacion.codigo_plc == payload.id_maquina, 
            Estacion.tenant_id == tenant_id
        )
    ).first()
    
    if not estacion:
        raise HTTPException(
            status_code=404, 
            detail=f"Máquina '{payload.id_maquina}' no configurada en este Tenant."
        )

    # 2. Clasificación de la Parada (Reglas Híbridas)
    delta = payload.detalles.delta_tiempo_segundos
    categoria = CategoriaParada.NORMAL
    
    if delta > 15.0:
        categoria = CategoriaParada.MICRO_MAYOR
    elif delta > 11.5:
        categoria = CategoriaParada.MICRO_MENOR

    # 3. Guardado Crudo (IoT Audit Log)
    ciclo = CicloProduccion(
        tenant_id=tenant_id,
        maquina_id=payload.id_maquina,
        timestamp=payload.timestamp,
        formato_moldes=payload.detalles.formato_moldes,
        panes_producidos=payload.detalles.panes_producidos,
        delta_tiempo_s=delta,
        categoria_parada=categoria
    )
    db.add(ciclo)

    # -------------------------------------------------------------
    # 4. INYECCIÓN OEE (La Magia de la Traducción Discreta)
    # -------------------------------------------------------------
    panes = payload.detalles.panes_producidos
    
    # Calculamos cuánto tardó cada pan individualmente dentro del ciclo
    tiempo_por_pan = delta / panes if panes > 0 else 0
    
    # Si el delta superó el umbral de alerta (ej. 300s = 5 min), 
    # generamos un "Hueco" (Downtime) para que el supervisor lo justifique.
    if delta > estacion.umbral_alerta:
        parada = ParadaDetectada(
            tenant_id=tenant_id,
            estacion_fk=estacion.id,
            inicio=payload.timestamp - timedelta(seconds=delta),
            fin=payload.timestamp,
            duracion_segundos=delta,
            estado=EstadoParada.PENDIENTE
        )
        db.add(parada)
        
        # Como fue una parada, reseteamos el tiempo del pan a 0 
        # para no castigar el Rendimiento injustamente (ya lo castigó la Disponibilidad)
        tiempo_por_pan = 0 

    # Generamos eventos virtuales para engañar positivamente a los Dashboards
    eventos_virtuales = []
    
    # Determinar el desempeño (Color en el dashboard) según los umbrales de la estación
    desempeno = "OPTIMO"
    if tiempo_por_pan > estacion.umbral_lento and tiempo_por_pan <= estacion.umbral_alerta:
        desempeno = "LENTO"
    elif tiempo_por_pan > estacion.umbral_alerta:
        desempeno = "ALERTA"

    for i in range(panes):
        # Generamos un código virtual rastreable (ej. PLC-ARMADORA-timestamp-1)
        barcode_virtual = f"PLC-{payload.id_maquina[:5].upper()}-{int(payload.timestamp.timestamp())}-{i}"
        
        # Espaciamos los timestamps virtualmente para que la línea de tiempo se vea limpia
        timestamp_virtual = payload.timestamp - timedelta(seconds=(tiempo_por_pan * i))
        
        ev = EventoEscaneo(
            tenant_id=tenant_id,
            barcode=barcode_virtual,
            estacion_fk=estacion.id,
            timestamp=timestamp_virtual,
            segundos_proceso=int(tiempo_por_pan),
            desempeno=desempeno
        )
        eventos_virtuales.append(ev)

    if eventos_virtuales:
        db.add_all(eventos_virtuales)

    # 5. Commit Transaccional Único (Todo o nada)
    db.commit()

    return {
        "status": "ok", 
        "mensaje": "Ciclo IoT procesado",
        "traduccion_oee": f"{len(eventos_virtuales)} eventos discretos generados."
    }
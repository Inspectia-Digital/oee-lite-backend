from pydantic import BaseModel, Field
from datetime import datetime

class PLCDetalles(BaseModel):
    formato_moldes: int = Field(..., description="Formato de la bandeja (4 o 5)")
    panes_producidos: int = Field(..., description="Cantidad de panes por ciclo")
    delta_tiempo_segundos: float = Field(..., description="Segundos desde el último ciclo")

class PLCPayload(BaseModel):
    timestamp: datetime
    id_maquina: str
    evento: str
    detalles: PLCDetalles
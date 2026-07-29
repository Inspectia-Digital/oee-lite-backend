import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List, Optional
from pydantic import BaseModel

from app.core.database import get_session
from app.core.auth import obtener_tenant_aislado
from app.models.domain import Planta, Linea, Estacion, TipoProduccion

router = APIRouter(prefix="/api/lite/jerarquia", tags=["Jerarquía Física"])

# --- Esquemas de Respuesta (Contratos para el Frontend) ---
# NOTA (Fase "API/CRUD completo"): estos ids eran `int`, pero Planta/Linea/
# Estacion usan UUID desde siempre -- bug real, cualquier llamada fallaba
# al serializar la respuesta (y linea_id: int en el path rechazaba
# cualquier UUID real con 422). Corregido.
class EstacionResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    tipo: str
    posicion_linea: int
    codigo_plc: Optional[str]

class LineaResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    tipo_produccion: TipoProduccion
    estaciones: List[EstacionResponse] = []

class PlantaResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    lineas: List[LineaResponse] = []

# --- Endpoints ---

@router.get("/plantas", response_model=List[PlantaResponse])
def listar_jerarquia_completa(
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    """
    Entrega toda la estructura del Tenant en una sola llamada (Ideal para cargar el estado global del Frontend).
    """
    # GET normal excluye inactivos (regla CRUD del HANDOFF); antes no filtraba nada.
    plantas_db = db.exec(select(Planta).where(Planta.tenant_id == tenant_id, Planta.activo == True)).all()  # noqa: E712

    resultado = []
    for p in plantas_db:
        lineas_db = db.exec(select(Linea).where(Linea.planta_id == p.id, Linea.activo == True)).all()  # noqa: E712
        lineas_list = []

        for l in lineas_db:
            estaciones_db = db.exec(
                select(Estacion)
                .where(Estacion.linea_id == l.id, Estacion.activa == True)  # noqa: E712
                .order_by(Estacion.posicion_linea)
            ).all()
            
            lineas_list.append(LineaResponse(
                id=l.id,
                nombre=l.nombre,
                tipo_produccion=l.tipo_produccion,
                estaciones=[EstacionResponse(**e.model_dump()) for e in estaciones_db]
            ))
            
        resultado.append(PlantaResponse(
            id=p.id,
            nombre=p.nombre,
            lineas=lineas_list
        ))
        
    return resultado

@router.get("/lineas/{linea_id}/estaciones", response_model=List[EstacionResponse])
def listar_estaciones_por_linea(
    linea_id: uuid.UUID,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    """
    Devuelve las estaciones ordenadas por posición física.
    """
    # 1. Validar que la línea pertenezca al tenant por seguridad cruzada
    linea = db.exec(select(Linea).where(Linea.id == linea_id, Linea.tenant_id == tenant_id)).first()
    if not linea:
        raise HTTPException(status_code=404, detail="Línea no encontrada o sin acceso")

    estaciones = db.exec(
        select(Estacion)
        .where(Estacion.linea_id == linea_id, Estacion.tenant_id == tenant_id, Estacion.activa == True)  # noqa: E712
        .order_by(Estacion.posicion_linea)
    ).all()
    
    return [EstacionResponse(**e.model_dump()) for e in estaciones]
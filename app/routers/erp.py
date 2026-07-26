from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from sqlmodel import Session, select
from datetime import date

from app.core.database import get_session
from app.core.auth import obtener_tenant_aislado
from app.models.domain import OrdenProduccion, MaestroSKU

router = APIRouter(prefix="/api/lite/erp", tags=["Integración ERP (Webhooks)"])

# ==========================================
# SCHEMAS (PYDANTIC)
# ==========================================
class OrdenERP(BaseModel):
    id_orden: str = Field(..., description="Número de Orden de Producción del ERP")
    codigo_sku: str = Field(..., description="Código del producto a fabricar (Debe existir en maestros)")
    cantidad_planeada: int = Field(..., gt=0, description="Cantidad objetivo a fabricar")
    fecha_planificada: date = Field(..., description="Fecha esperada de inicio de producción")
    cliente: Optional[str] = Field(default=None, description="Cliente final (Opcional)")

class SincronizacionERPRequest(BaseModel):
    ordenes: List[OrdenERP]

# ==========================================
# ENDPOINTS CORE (M2M)
# ==========================================
@router.post("/orders", status_code=status.HTTP_200_OK)
def sincronizar_ordenes_erp(
    payload: SincronizacionERPRequest,
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    """
    Webhook para ingestar el plan de producción desde un ERP externo.
    Realiza un 'Upsert': crea las órdenes nuevas y actualiza las existentes.
    """
    if not payload.ordenes:
        raise HTTPException(status_code=400, detail="El payload no contiene órdenes.")

    creadas = 0
    actualizadas = 0
    skus_faltantes = set()

    # 1. Cargar caché de SKUs del tenant para evitar consultas N+1 en la BBDD
    skus_db = db.exec(select(MaestroSKU.codigo_sku).where(MaestroSKU.tenant_id == tenant_id)).all()
    skus_validos = set(skus_db)

    for orden_erp in payload.ordenes:
        # 2. Validación de integridad referencial
        if orden_erp.codigo_sku not in skus_validos:
            skus_faltantes.add(orden_erp.codigo_sku)
            continue # Se salta la OP si el SKU no fue precargado por el cliente

        # 3. Buscar si la orden ya existe (Upsert Logic)
        orden_db = db.exec(
            select(OrdenProduccion)
            .where(
                OrdenProduccion.id_orden == orden_erp.id_orden,
                OrdenProduccion.tenant_id == tenant_id
            )
        ).first()

        if orden_db:
            # Update (Actualizamos plan)
            orden_db.sku_fk = orden_erp.codigo_sku
            orden_db.cantidad_esperada = orden_erp.cantidad_planeada
            orden_db.plan_fecha = str(orden_erp.fecha_planificada)
            actualizadas += 1
        else:
            # Insert (Nueva orden)
            nueva_orden = OrdenProduccion(
                tenant_id=tenant_id,
                id_orden=orden_erp.id_orden,
                sku_fk=orden_erp.codigo_sku,
                cantidad_esperada=orden_erp.cantidad_planeada,
                plan_fecha=str(orden_erp.fecha_planificada),
                estado="PENDIENTE"
            )
            db.add(nueva_orden)
            creadas += 1

    # 4. Commit transaccional masivo
    db.commit()

    return {
        "status": "ok",
        "mensaje": "Sincronización del plan de producción completada",
        "resultados": {
            "creadas": creadas,
            "actualizadas": actualizadas,
            "skus_ignorados_por_no_existir": list(skus_faltantes)
        }
    }
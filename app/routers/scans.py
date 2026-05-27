from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from sqlmodel import Session, select
from datetime import datetime

from app.core.database import get_session
from app.core.auth import get_usuario_actual 
from app.models.domain import EventoEscaneo, UsuarioSaaS, Estacion, Operario, Tenant

router = APIRouter(prefix="/api/lite", tags=["Ingesta de Datos (Terminales)"])

# ==========================================
# MOLDES (SCHEMAS)
# ==========================================
class ScanRequest(BaseModel):
    codigo_pieza: str
    estacion_id: str
    legajo_operario: Optional[str] = None # Solo se envía si el Tenant está en "modo escaneo"

# ==========================================
# ENDPOINTS DE VALIDACIÓN (Pasos 1 y 2)
# ==========================================

@router.get("/estaciones/{estacion_id}/validar")
def validar_estacion_terminal(
    estacion_id: str,
    db: Session = Depends(get_session),
    terminal: UsuarioSaaS = Depends(get_usuario_actual)
):
    """
    Paso 1: La terminal escanea una máquina para bloquearse en ella.
    Validamos que exista y le devolvemos la configuración de la empresa.
    """
    # 1. Buscamos la estación asegurando que sea de la misma empresa que la terminal
    estacion = db.exec(select(Estacion).where(
        Estacion.id == estacion_id, 
        Estacion.tenant_id == terminal.tenant_id
    )).first()
    
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada o no pertenece a esta planta.")
        
    # 2. Buscamos la configuración de la empresa para decirle a la terminal cómo comportarse
    tenant = db.exec(select(Tenant).where(Tenant.id == terminal.tenant_id)).first()
    
    return {
        "status": "ok", 
        "estacion": {
            "id": estacion.id,
            "nombre": estacion.nombre
        },
        "configuracion": {
            "modo_asignacion_operarios": tenant.modo_asignacion_operarios if tenant else "manual"
        }
    }

@router.get("/operarios/{legajo}/validar")
def validar_credencial_operario(
    legajo: str,
    db: Session = Depends(get_session),
    terminal: UsuarioSaaS = Depends(get_usuario_actual)
):
    """
    Paso 2 (Opcional): Si la empresa exige escaneo de credencial, 
    la terminal valida que el humano exista en la base de datos.
    """
    operario = db.exec(select(Operario).where(
        Operario.legajo == legajo,
        Operario.tenant_id == terminal.tenant_id
    )).first()
    
    if not operario:
        raise HTTPException(status_code=404, detail="Credencial inválida o no registrada.")
        
    return {
        "status": "ok",
        "operario": {
            "id": operario.id,
            "nombre_completo": operario.nombre_completo,
            "legajo": operario.legajo
        }
    }

# ==========================================
# ENDPOINT CORE DE INGESTA (Paso 3)
# ==========================================

@router.post("/scans", status_code=status.HTTP_201_CREATED)
def registrar_escaneo_rapido(
    scan: ScanRequest,
    db: Session = Depends(get_session),
    terminal: UsuarioSaaS = Depends(get_usuario_actual) 
):
    """
    Paso 3: Alta velocidad. Registra el escaneo del colchón.
    Diseñado para responder en < 50ms.
    """
    # 1. Validaciones ultrarrápidas de integridad
    estacion = db.exec(select(Estacion).where(Estacion.id == scan.estacion_id)).first()
    if not estacion or str(estacion.tenant_id) != str(terminal.tenant_id):
        raise HTTPException(status_code=400, detail="Estación inválida.")

    operario_id_real = None
    if scan.legajo_operario:
        operario = db.exec(select(Operario).where(Operario.legajo == scan.legajo_operario)).first()
        if operario and str(operario.tenant_id) == str(terminal.tenant_id):
            operario_id_real = operario.id

    # 2. Inyección Stateless y Creación del Evento
    # Usamos tu modelo EventoEscaneo existente
    nuevo_evento = EventoEscaneo(
        tenant_id=terminal.tenant_id,
        barcode=scan.codigo_pieza,
        estacion_fk=estacion.id,
        operario_fk=operario_id_real,
        # La cuenta Auth0 de la terminal queda implícita (podrías agregar auth0_id al modelo si lo deseas)
        timestamp=datetime.utcnow()
    )
    
    # 3. Persistencia
    db.add(nuevo_evento)
    db.commit()
    
    # Nota Arquitectónica: 
    # El cálculo de "segundos_proceso" y "desempeno" (OPTIMO/LENTO/ALERTA) 
    # que tenías en operacion.py lo ideal es moverlo a un BackgroundTask 
    # o procesarlo aquí mismo si es estrictamente necesario, pero para mantener 
    # este endpoint en < 50ms, lo ideal es solo insertar el dato crudo.
    
    return {"status": "ok", "mensaje": "Escaneo registrado exitosamente"}
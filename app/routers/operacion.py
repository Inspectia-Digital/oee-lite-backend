from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from datetime import datetime
import uuid
from pydantic import BaseModel

from app.core.database import get_session
# 1. IMPORTAMOS LA DEPENDENCIA MÁGICA DE AISLAMIENTO
from app.core.auth import obtener_tenant_aislado
# 2. IMPORTAMOS LOS MODELOS NECESARIOS (Agregamos Tenant y MaestroSKU para el DTR)
from app.models.domain import (
    EventoEscaneo, Estacion, Operario, Turno, AsignacionTurno, 
    ParadaDetectada, MotivoParada, EstadoParada, Tenant, MaestroSKU
)

router = APIRouter(tags=["Operacion"])

# ==========================================
# --- MOLDES PYDANTIC ---
# ==========================================
class BarcodeDecodificado(BaseModel):
    secuencia: str
    orden_produccion: str
    codigo_sku: str
    codigo_original: str

class ClasificarParada(BaseModel):
    motivo_fk: uuid.UUID

class AsignacionRetroactiva(BaseModel):
    estacion_fk: uuid.UUID
    operario_fk: uuid.UUID
    inicio: datetime
    fin: datetime

class ParadaPlanificadaCreate(BaseModel):
    estacion_fk: uuid.UUID
    motivo_fk: uuid.UUID
    inicio: datetime
    fin: datetime

# ==========================================
# --- HELPER FUNCTIONS ---
# ==========================================
def parsear_barcode(barcode: str) -> BarcodeDecodificado:
    """Descompone el código de 25 caracteres estándar de la fábrica."""
    barcode = barcode.strip()
    if len(barcode) < 25:
        raise ValueError(f"Código corto ({len(barcode)} caracteres). Se esperaban 25.")

    return BarcodeDecodificado(
        secuencia=barcode[0:3],
        orden_produccion=barcode[3:11],
        codigo_sku=barcode[11:],
        codigo_original=barcode
    )

@router.get("/test-parser/{barcode}", tags=["Pruebas"])
def probar_parser(
    barcode: str, 
    tenant_id: str = Depends(obtener_tenant_aislado) # Protegemos hasta los tests
):
    try:
        return {"status": "ok", "data": parsear_barcode(barcode)}
    except Exception as e:
        return {"status": "error", "detalle": str(e)}

# ==========================================
# ENDPOINTS BLINDADOS (CORE BUSINESS LOGIC)
# ==========================================

@router.post("/eventos/", response_model=EventoEscaneo)
def registrar_evento(
    evento: EventoEscaneo, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- B2B ISOLATION
):
    # 🔒 Forzamos el tenant_id interceptado por seguridad
    evento.tenant_id = tenant_id

    if isinstance(evento.timestamp, str):
        evento.timestamp = datetime.fromisoformat(evento.timestamp.replace("Z", ""))

    # 🔒 Validamos que la estación pertenezca a la empresa
    estacion = db.get(Estacion, evento.estacion_fk)
    if not estacion or estacion.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Estación no encontrada o no pertenece a su empresa")

    # ==============================================================
    # FLUJO A: LOGIN DE OPERARIO (Barcode inicia con OP-)
    # ==============================================================
    if evento.barcode.startswith("OP-"):
        operario = db.exec(
            select(Operario).where(
                Operario.legajo == evento.barcode, 
                Operario.tenant_id == tenant_id
            )
        ).first()
        
        if not operario:
            raise HTTPException(status_code=404, detail="Credencial de operario no reconocida en su empresa")
        
        hora_actual = evento.timestamp.time()
        
        turno_actual = db.exec(
            select(Turno).where(
                Turno.tenant_id == tenant_id,
                Turno.hora_inicio <= hora_actual, 
                Turno.hora_fin >= hora_actual
            )
        ).first()

        if not turno_actual:
            raise HTTPException(status_code=400, detail="No hay un turno configurado para esta hora")

        nueva_asig = AsignacionTurno(
            tenant_id=tenant_id,
            fecha=evento.timestamp.date(),
            estacion_fk=estacion.id,
            operario_fk=operario.id,
            turno_fk=turno_actual.id
        )
        db.add(nueva_asig)
        db.commit()
        
        evento.desempeno = "LOGIN_OPERARIO"
        return evento

    # ==============================================================
    # FLUJO B: PROCESO NORMAL DE ESCANEO DE PIEZA (Motor DTR)
    # ==============================================================
    datos_barcode = parsear_barcode(evento.barcode)
    evento.orden_fk = datos_barcode.orden_produccion

    # 1. Resolver Asignación de Operario actual
    hora_actual = evento.timestamp.time()
    fecha_actual = evento.timestamp.date()

    asignacion_hoy = db.exec(
        select(AsignacionTurno, Turno)
        .join(Turno, AsignacionTurno.turno_fk == Turno.id)
        .where(
            AsignacionTurno.tenant_id == tenant_id,
            AsignacionTurno.estacion_fk == estacion.id,
            AsignacionTurno.fecha == fecha_actual,
            Turno.hora_inicio <= hora_actual,
            Turno.hora_fin >= hora_actual
        )
    ).first()

    if asignacion_hoy:
        asignacion, turno = asignacion_hoy
        evento.operario_fk = asignacion.operario_fk

    # 2. RESOLUCIÓN DINÁMICA DE UMBRALES (DTR)
    tenant = db.get(Tenant, tenant_id)
    sku = db.exec(select(MaestroSKU).where(
        MaestroSKU.codigo_sku == datos_barcode.codigo_sku,
        MaestroSKU.tenant_id == tenant_id
    )).first()

    if sku and tenant:
        # Modo Dinámico: Lee tolerancias maestras de la empresa (fallback default safe)
        t_optimo = sku.tiempo_ciclo_teorico
        t_lento = t_optimo * getattr(tenant, 'tolerancia_lento_pct', 1.15)
        t_alerta = t_optimo * getattr(tenant, 'tolerancia_alerta_pct', 1.25)
    else:
        # Fallback Estático: Configuración hardcodeada en la estación si el SKU es nuevo/desconocido
        t_optimo = estacion.umbral_optimo
        t_lento = estacion.umbral_lento
        t_alerta = estacion.umbral_alerta

    # 3. Cálculo Deductivo OEE (Tiempo de Ciclo)
    ultimo_evento = db.exec(
        select(EventoEscaneo)
        .where(
            EventoEscaneo.tenant_id == tenant_id, 
            EventoEscaneo.estacion_fk == estacion.id # FIX: Filtramos por Estación, no por barcode
        )
        .order_by(EventoEscaneo.timestamp.desc())
    ).first()

    if ultimo_evento:
        diff_segundos = (evento.timestamp - ultimo_evento.timestamp).total_seconds()
        evento.segundos_proceso = int(diff_segundos) 
        
        # Clasificación OEE según Motor DTR
        if diff_segundos > t_alerta: 
            # Downtime Detectado
            evento.desempeno = "ALERTA"
            nueva_parada = ParadaDetectada(
                tenant_id=tenant_id, 
                estacion_fk=estacion.id,
                inicio=ultimo_evento.timestamp, 
                fin=evento.timestamp,
                duracion_segundos=diff_segundos, 
                estado=EstadoParada.PENDIENTE
            )
            db.add(nueva_parada)
            
            # Protección de Rendimiento: Capeamos el tiempo al ideal para no doble-castigar OEE
            evento.segundos_proceso = int(t_optimo) 

        elif diff_segundos <= t_optimo:
            evento.desempeno = "OPTIMO"
        elif diff_segundos <= t_lento:
            evento.desempeno = "LENTO"
        else:
            evento.desempeno = "ALERTA"
            # Si superó umbral, no es parada crítica, y es estación de calidad -> Defecto
            if estacion.tipo.lower() == "calidad":
                evento.es_retrabajo = True
    else:
        # Primer colchón del turno
        evento.desempeno = "INICIO"
        evento.segundos_proceso = 0

    db.add(evento)
    db.commit()
    db.refresh(evento)
    return evento


@router.post("/operarios/asignar-retroactivo/")
def asignar_operario_retroactivo(
    datos: AsignacionRetroactiva, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    operario = db.get(Operario, datos.operario_fk)
    if not operario or operario.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Operario no encontrado en su empresa")

    eventos = db.exec(
        select(EventoEscaneo).where(
            EventoEscaneo.tenant_id == tenant_id,
            EventoEscaneo.estacion_fk == datos.estacion_fk,
            EventoEscaneo.timestamp >= datos.inicio,
            EventoEscaneo.timestamp <= datos.fin
        )
    ).all()

    if not eventos:
        return {"mensaje": "No se encontraron eventos en ese rango.", "actualizados": 0}

    for evento in eventos:
        evento.operario_fk = operario.id
        db.add(evento)

    db.commit()

    return {
        "mensaje": f"Se asignaron {len(eventos)} escaneos a {operario.nombre_completo}", 
        "actualizados": len(eventos)
    }


@router.get("/paradas/pendientes/", response_model=list[ParadaDetectada])
def obtener_paradas_pendientes(
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    return db.exec(
        select(ParadaDetectada)
        .where(
            ParadaDetectada.tenant_id == tenant_id,
            ParadaDetectada.estado == EstadoParada.PENDIENTE
        )
    ).all()


@router.patch("/paradas/{parada_id}/clasificar", response_model=ParadaDetectada)
def clasificar_parada(
    parada_id: uuid.UUID, 
    datos: ClasificarParada, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    parada = db.get(ParadaDetectada, parada_id)
    if not parada or parada.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Parada no encontrada en su empresa")
    
    motivo = db.get(MotivoParada, datos.motivo_fk)
    if not motivo or motivo.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Motivo de parada no válido o no autorizado")

    parada.motivo_fk = motivo.id
    parada.estado = EstadoParada.CLASIFICADA 
    
    db.add(parada)
    db.commit()
    db.refresh(parada)
    return parada


@router.post("/paradas/planificadas/", response_model=ParadaDetectada)
def registrar_parada_planificada(
    datos: ParadaPlanificadaCreate, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    estacion = db.get(Estacion, datos.estacion_fk)
    if not estacion or estacion.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Estación no encontrada")

    motivo = db.get(MotivoParada, datos.motivo_fk)
    if not motivo or motivo.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Motivo no encontrado")
        
    if str(motivo.tipo_parada).lower().replace("tipoparada.", "") != "planificada":
        raise HTTPException(status_code=400, detail="El motivo seleccionado no es del tipo PLANIFICADA")
    
    duracion = (datos.fin - datos.inicio).total_seconds()
    if duracion <= 0:
         raise HTTPException(status_code=400, detail="La fecha de fin debe ser mayor a la de inicio")

    nueva_parada = ParadaDetectada(
        tenant_id=tenant_id,
        estacion_fk=datos.estacion_fk,
        motivo_fk=motivo.id,
        inicio=datos.inicio,
        fin=datos.fin,
        duracion_segundos=duracion,
        estado=EstadoParada.CLASIFICADA
    )
    
    db.add(nueva_parada)
    db.commit()
    db.refresh(nueva_parada)
    
    return nueva_parada


@router.post("/asignaciones/", response_model=AsignacionTurno)
def crear_asignacion(
    asignacion: AsignacionTurno, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado)
):
    asignacion.tenant_id = tenant_id
    db.add(asignacion)
    db.commit()
    db.refresh(asignacion)
    return asignacion
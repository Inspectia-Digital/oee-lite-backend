from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.core.database import get_session
# 1. IMPORTAMOS LA NUEVA DEPENDENCIA MAGICA
from app.core.auth import obtener_tenant_aislado
from app.models.domain import (
    EventoEscaneo, Estacion, Operario, Turno, AsignacionTurno, 
    ParadaDetectada, MotivoParada, EstadoParada
)
from pydantic import BaseModel
from datetime import datetime
import uuid

router = APIRouter(tags=["Operacion"])

# --- MOLDES ---
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

# --- HELPER ---
def parsear_barcode(barcode: str) -> BarcodeDecodificado:
    """Descompone el código de 25 caracteres de la fábrica."""
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
    tenant_id: str = Depends(obtener_tenant_aislado) # Protegemos hasta las pruebas
):
    try:
        return {"status": "ok", "data": parsear_barcode(barcode)}
    except Exception as e:
        return {"status": "error", "detalle": str(e)}


# ==========================================
# ENDPOINTS BLINDADOS (SOPORTAN "MODO DIOS")
# ==========================================

@router.post("/eventos/", response_model=EventoEscaneo)
def registrar_evento(
    evento: EventoEscaneo, 
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    # 🔒 Forzamos el tenant_id interceptado
    evento.tenant_id = tenant_id

    if isinstance(evento.timestamp, str):
        evento.timestamp = datetime.fromisoformat(evento.timestamp.replace("Z", ""))

    # 🔒 Validamos que la estación pertenezca a este tenant
    estacion = db.get(Estacion, evento.estacion_fk)
    if not estacion or estacion.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Estación no encontrada o no pertenece a su empresa")

    # ==============================================================
    # --- LOGIN DE OPERARIO ---
    # ==============================================================
    if evento.barcode.startswith("OP-"):
        # 🔒 Buscamos al operario asegurando que sea de la empresa
        operario = db.exec(
            select(Operario).where(
                Operario.legajo == evento.barcode, 
                Operario.tenant_id == tenant_id
            )
        ).first()
        
        if not operario:
            raise HTTPException(status_code=404, detail="Credencial de operario no reconocida en su empresa")
        
        hora_actual = evento.timestamp.time()
        
        # 🔒 Buscamos el turno de la empresa
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
    # --- PROCESO NORMAL DE ESCANEO DE COLCHÓN ---
    # ==============================================================
    datos_barcode = parsear_barcode(evento.barcode)
    evento.orden_fk = datos_barcode.orden_produccion

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

    ultimo_evento = db.exec(
        select(EventoEscaneo)
        .where(
            EventoEscaneo.tenant_id == tenant_id, 
            EventoEscaneo.barcode == evento.barcode
        )
        .order_by(EventoEscaneo.timestamp.desc())
    ).first()

    if ultimo_evento:
        diff_segundos = (evento.timestamp - ultimo_evento.timestamp).total_seconds()
        evento.segundos_proceso = int(diff_segundos) 
        
        if diff_segundos > 150: 
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
        elif diff_segundos <= estacion.umbral_optimo:
            evento.desempeno = "OPTIMO"
        elif diff_segundos <= estacion.umbral_lento:
            evento.desempeno = "LENTO"
        else:
            evento.desempeno = "ALERTA"
            if estacion.tipo.lower() == "calidad":
                evento.es_retrabajo = True
    else:
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
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    # 🔒 Validamos que el operario exista y sea de esta empresa
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
        return {"mensaje": "No se encontraron colchones en ese rango de tiempo para esta estación.", "actualizados": 0}

    for evento in eventos:
        evento.operario_fk = operario.id
        db.add(evento)

    db.commit()

    return {
        "mensaje": f"Se asignaron {len(eventos)} colchones a {operario.nombre_completo}", 
        "actualizados": len(eventos)
    }

@router.get("/paradas/pendientes/", response_model=list[ParadaDetectada])
def obtener_paradas_pendientes(
    db: Session = Depends(get_session),
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
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
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    # 🔒 Verificamos que la parada exista y pertenezca a la empresa
    parada = db.get(ParadaDetectada, parada_id)
    if not parada or parada.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Parada no encontrada en su empresa")
    
    # 🔒 Verificamos que el motivo de parada pertenezca a la empresa
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
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    # 🔒 Validamos estación
    estacion = db.get(Estacion, datos.estacion_fk)
    if not estacion or estacion.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Estación no encontrada")

    # 🔒 Validamos motivo
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
    tenant_id: str = Depends(obtener_tenant_aislado) # <-- APLICADO
):
    # 🔒 Forzamos el tenant
    asignacion.tenant_id = tenant_id
    
    # 🔒 Opcional: Podrías verificar que estación, operario y turno sean de esta empresa.
    # Por ahora confiaremos en que los IDs que envíe el Front (ya filtrados) son correctos,
    # pero forzamos el tenant del registro padre.
    
    db.add(asignacion)
    db.commit()
    db.refresh(asignacion)
    return asignacion
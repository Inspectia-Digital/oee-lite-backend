import logging

from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile, File, status
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import List, Optional
from datetime import time
import uuid
import pandas as pd
import io

logger = logging.getLogger(__name__)

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext, get_usuario_actual
from app.models.domain import (
    Estacion, MotivoParada, Operario, Turno, MaestroSKU, OrdenProduccion,
    Linea, Supervisor, TipoParada, RolUsuario, Planta, ModoAsignacionOperarios, ModoAsignacionOperariosEstacion, UsuarioSaaS,
    Maquina, MaquinaEstacion,
)

router = APIRouter(prefix="/config", tags=["Configuración y Maestros"])

# Fase K (auditoría QA #7): mismo criterio que en importaciones.py.
MAX_UPLOAD_BYTES_ERP = 5 * 1024 * 1024  # 5 MiB
MAX_FILAS_IMPORT_ERP = 20_000

# ==========================================
# 🛡️ MIDDLEWARE LOCAL (RBAC)
# ==========================================
def requerir_gerencia(usuario: UsuarioSaaS = Depends(get_usuario_actual)):
    """Asegura que solo Gerencia o SuperAdmin puedan modificar la configuración de la planta."""
    if usuario.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA]:
        raise HTTPException(status_code=403, detail="Acceso denegado. Requiere privilegios de Gerencia.")
    return usuario


def _requerir_permiso_inactivos(incluir_inactivos: bool, usuario: UsuarioSaaS):
    """GET normal excluye inactivos; incluir_inactivos=true sólo Gerencia/SuperAdmin."""
    if incluir_inactivos and usuario.rol not in (RolUsuario.SUPERADMIN, RolUsuario.GERENCIA):
        raise HTTPException(status_code=403, detail="Sólo Gerencia o SuperAdmin pueden ver registros inactivos.")

# ==========================================
# 📦 SCHEMAS SEGUROS (Evitan inyección de IDs)
# ==========================================
class LineaCreate(BaseModel):
    nombre: str
    # Opcional: si no viene, se usa la planta activa (X-Sub-Tenant-Id).
    # El front ya trackea "planta activa" vía ese header (switcher del
    # TopBar) y no la manda en el body de creación -- exigirla acá
    # duplicaba la misma info en dos lugares y bloqueaba crear líneas.
    planta_id: Optional[uuid.UUID] = None
    modo_asignacion_operarios: Optional[ModoAsignacionOperarios] = ModoAsignacionOperarios.MANUAL

class LineaUpdate(BaseModel):
    nombre: Optional[str] = None
    modo_asignacion_operarios: Optional[ModoAsignacionOperarios] = None
    tipo_produccion: Optional[str] = None
    metodo_calidad: Optional[str] = None
    activo: Optional[bool] = None

class EstacionCreate(BaseModel):
    nombre: str
    tipo: str
    linea_id: Optional[uuid.UUID] = None
    parent_id: Optional[uuid.UUID] = None
    posicion_linea: int = 1
    ramal: str = "Principal"
    umbral_optimo: int = 240
    umbral_lento: int = 280
    umbral_alerta: int = 300
    codigo_plc: Optional[str] = None
    modo_asignacion_operarios: Optional[ModoAsignacionOperariosEstacion] = ModoAsignacionOperariosEstacion.HEREDAR

class EstacionUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[str] = None
    umbral_optimo: Optional[int] = None
    umbral_lento: Optional[int] = None
    umbral_alerta: Optional[int] = None
    activa: Optional[bool] = None
    posicion_linea: Optional[int] = None
    ramal: Optional[str] = None
    codigo_plc: Optional[str] = None

class MotivoParadaCreate(BaseModel):
    nombre: str
    tipo_parada: TipoParada

class MotivoParadaUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo_parada: Optional[TipoParada] = None
    activo: Optional[bool] = None

class TurnoCreate(BaseModel):
    nombre: str
    hora_inicio: time
    hora_fin: time
    descanso_minutos: int = 0
    linea_id: Optional[uuid.UUID] = None

class TurnoUpdate(BaseModel):
    nombre: Optional[str] = None
    hora_inicio: Optional[time] = None
    hora_fin: Optional[time] = None
    descanso_minutos: Optional[int] = None
    activo: Optional[bool] = None

class MaquinaCreate(BaseModel):
    codigo_externo: str
    nombre: Optional[str] = None

class MaquinaUpdate(BaseModel):
    codigo_externo: Optional[str] = None
    nombre: Optional[str] = None
    activo: Optional[bool] = None

class MaquinaEstacionCreate(BaseModel):
    estacion_id: uuid.UUID


# ==========================================
# 🏭 ABM DE LÍNEAS (Con Validación Cross-Tenant)
# ==========================================
@router.post("/lineas/", response_model=Linea, status_code=status.HTTP_201_CREATED)
def crear_linea(
    payload: LineaCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    planta_id = payload.planta_id or (uuid.UUID(context.sub_tenant_id) if context.sub_tenant_id else None)
    if not planta_id:
        raise HTTPException(
            status_code=400,
            detail="Falta planta_id (o seleccioná una planta activa en el selector) para crear la línea.",
        )

    # Cross-Tenant Validation: Evita asignar la línea a una planta de otra empresa
    planta_db = db.exec(select(Planta).where(Planta.id == planta_id, Planta.tenant_id == context.tenant_id)).first()
    if not planta_db:
        raise HTTPException(status_code=400, detail="La planta no existe o pertenece a otra organización.")

    datos = payload.model_dump(exclude={"planta_id"})
    nueva_linea = Linea(tenant_id=context.tenant_id, planta_id=planta_id, **datos)
    db.add(nueva_linea)
    db.commit()
    db.refresh(nueva_linea)
    return nueva_linea

@router.get("/lineas/", response_model=List[Linea])
def obtener_lineas(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(Linea).where(Linea.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(Linea.activo == True)  # noqa: E712
    return db.exec(query).all()

@router.get("/lineas/{linea_id}", response_model=Linea)
def obtener_linea(
    linea_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    linea = db.exec(select(Linea).where(Linea.id == linea_id, Linea.tenant_id == context.tenant_id)).first()
    if not linea:
        raise HTTPException(status_code=404, detail="Línea no encontrada.")
    return linea

@router.patch("/lineas/{linea_id}", response_model=Linea)
def actualizar_linea(
    linea_id: uuid.UUID,
    payload: LineaUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    linea = db.exec(select(Linea).where(Linea.id == linea_id, Linea.tenant_id == context.tenant_id)).first()
    if not linea:
        raise HTTPException(status_code=404, detail="Línea no encontrada.")
    datos = payload.model_dump(exclude_unset=True)
    for key, value in datos.items():
        setattr(linea, key, value)
    db.add(linea)
    db.commit()
    db.refresh(linea)
    return linea

@router.delete("/lineas/{linea_id}")
def desactivar_linea(
    linea_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    """Baja lógica. Nunca hard-delete: hay estaciones y eventos históricos que dependen de esta línea."""
    linea = db.exec(select(Linea).where(Linea.id == linea_id, Linea.tenant_id == context.tenant_id)).first()
    if not linea:
        raise HTTPException(status_code=404, detail="Línea no encontrada.")
    linea.activo = False
    db.add(linea)
    db.commit()
    return {"mensaje": "Línea desactivada."}


# ==========================================
# ⚙️ ABM DE ESTACIONES
# ==========================================
@router.post("/estaciones/", response_model=Estacion, status_code=status.HTTP_201_CREATED)
def crear_estacion(
    payload: EstacionCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    if payload.linea_id:
        linea_db = db.exec(select(Linea).where(Linea.id == payload.linea_id, Linea.tenant_id == context.tenant_id)).first()
        if not linea_db: raise HTTPException(status_code=400, detail="Línea inválida.")

    nueva_estacion = Estacion(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nueva_estacion)
    db.commit()
    db.refresh(nueva_estacion)
    return nueva_estacion

@router.get("/estaciones/", response_model=List[Estacion])
def obtener_estaciones(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(Estacion).where(Estacion.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(Estacion.activa == True)  # noqa: E712
    return db.exec(query).all()

@router.get("/estaciones/{estacion_id}", response_model=Estacion)
def obtener_estacion(
    estacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    estacion = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada.")
    return estacion

@router.patch("/estaciones/{estacion_id}", response_model=Estacion)
def actualizar_estacion(
    estacion_id: uuid.UUID,
    payload: EstacionUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    estacion_db = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion_db: raise HTTPException(status_code=404, detail="Estación no encontrada")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items(): setattr(estacion_db, key, value)

    db.add(estacion_db)
    db.commit()
    db.refresh(estacion_db)
    return estacion_db

@router.delete("/estaciones/{estacion_id}")
def eliminar_estacion(
    estacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    """Baja lógica (activa=False). Antes era DELETE físico (con un try/except
    IntegrityError como único freno) -- violaba "cero hard-deletes"; corregido."""
    estacion_db = db.exec(select(Estacion).where(Estacion.id == estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion_db: raise HTTPException(status_code=404, detail="Estación no encontrada")

    estacion_db.activa = False
    db.add(estacion_db)
    db.commit()
    return {"mensaje": "Estación desactivada."}


# ==========================================
# 🛑 ABM DE MOTIVOS DE PARADA
# ==========================================
@router.post("/motivos-parada/", response_model=MotivoParada, status_code=status.HTTP_201_CREATED)
def crear_motivo_parada(
    payload: MotivoParadaCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    nuevo_motivo = MotivoParada(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nuevo_motivo)
    db.commit()
    db.refresh(nuevo_motivo)
    return nuevo_motivo

@router.get("/motivos-parada/", response_model=List[MotivoParada])
def obtener_motivos_parada(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(MotivoParada).where(MotivoParada.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(MotivoParada.activo == True)  # noqa: E712
    return db.exec(query).all()

@router.get("/motivos-parada/{motivo_id}", response_model=MotivoParada)
def obtener_motivo_parada(
    motivo_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    motivo = db.exec(select(MotivoParada).where(MotivoParada.id == motivo_id, MotivoParada.tenant_id == context.tenant_id)).first()
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo no encontrado.")
    return motivo

@router.patch("/motivos-parada/{motivo_id}", response_model=MotivoParada)
def actualizar_motivo_parada(
    motivo_id: uuid.UUID,
    payload: MotivoParadaUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    motivo = db.exec(select(MotivoParada).where(MotivoParada.id == motivo_id, MotivoParada.tenant_id == context.tenant_id)).first()
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo no encontrado.")
    datos = payload.model_dump(exclude_unset=True)
    for key, value in datos.items():
        setattr(motivo, key, value)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    return motivo

@router.delete("/motivos-parada/{motivo_id}")
def desactivar_motivo_parada(
    motivo_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    motivo = db.exec(select(MotivoParada).where(MotivoParada.id == motivo_id, MotivoParada.tenant_id == context.tenant_id)).first()
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo no encontrado.")
    motivo.activo = False
    db.add(motivo)
    db.commit()
    return {"mensaje": "Motivo de parada desactivado."}


# ==========================================
# ⏱️ ABM DE TURNOS
# ==========================================
@router.post("/turnos/", response_model=Turno, status_code=status.HTTP_201_CREATED)
def crear_turno(
    payload: TurnoCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    if payload.linea_id:
        linea_db = db.exec(select(Linea).where(Linea.id == payload.linea_id, Linea.tenant_id == context.tenant_id)).first()
        if not linea_db:
            raise HTTPException(status_code=400, detail="Línea inválida o de otra organización.")

    nuevo_turno = Turno(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nuevo_turno)
    db.commit()
    db.refresh(nuevo_turno)
    return nuevo_turno

@router.get("/turnos/", response_model=List[Turno])
def obtener_turnos(
    linea_id: Optional[uuid.UUID] = None,
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(Turno).where(Turno.tenant_id == context.tenant_id)
    if linea_id: query = query.where(Turno.linea_id == linea_id)
    if not incluir_inactivos:
        query = query.where(Turno.activo == True)  # noqa: E712
    return db.exec(query).all()

@router.get("/turnos/{turno_id}", response_model=Turno)
def obtener_turno(
    turno_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    turno = db.exec(select(Turno).where(Turno.id == turno_id, Turno.tenant_id == context.tenant_id)).first()
    if not turno:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")
    return turno

@router.patch("/turnos/{turno_id}", response_model=Turno)
def actualizar_turno(
    turno_id: uuid.UUID,
    payload: TurnoUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    turno = db.exec(select(Turno).where(Turno.id == turno_id, Turno.tenant_id == context.tenant_id)).first()
    if not turno:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")
    datos = payload.model_dump(exclude_unset=True)
    for key, value in datos.items():
        setattr(turno, key, value)
    db.add(turno)
    db.commit()
    db.refresh(turno)
    return turno

@router.delete("/turnos/{turno_id}")
def desactivar_turno(
    turno_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    turno = db.exec(select(Turno).where(Turno.id == turno_id, Turno.tenant_id == context.tenant_id)).first()
    if not turno:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")
    turno.activo = False
    db.add(turno)
    db.commit()
    return {"mensaje": "Turno desactivado."}


# ==========================================
# 📊 ERP INTEGRATION: IMPORTACIÓN MASIVA (CERO FRICCIÓN)
# ==========================================
@router.post("/erp/skus/bulk", tags=["Integración ERP"])
async def importar_skus_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia)
):
    """Importa o actualiza el Maestro de SKUs desde un CSV (codigo_sku, descripcion, tiempo_ciclo_teorico)."""
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Debe ser un archivo CSV")

    # Fase K (auditoría QA #7): límite de tamaño -- lee como máximo un byte
    # de más en vez de bufferizar un archivo arbitrariamente grande entero.
    contenido = await file.read(MAX_UPLOAD_BYTES_ERP + 1)
    if len(contenido) > MAX_UPLOAD_BYTES_ERP:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo supera el límite de {MAX_UPLOAD_BYTES_ERP // (1024 * 1024)} MiB.",
        )
    try:
        df = pd.read_csv(io.BytesIO(contenido))
        if len(df) > MAX_FILAS_IMPORT_ERP:
            raise HTTPException(
                status_code=400,
                detail=f"El archivo tiene {len(df)} filas; el máximo soportado por carga es {MAX_FILAS_IMPORT_ERP}.",
            )
        requeridos = {'codigo_sku', 'descripcion', 'tiempo_ciclo_teorico'}
        if not requeridos.issubset(df.columns):
            raise ValueError(f"Faltan columnas requeridas: {requeridos - set(df.columns)}")

        skus_procesados = 0
        for _, row in df.iterrows():
            sku_db = db.exec(select(MaestroSKU).where(
                MaestroSKU.codigo_sku == str(row['codigo_sku']),
                MaestroSKU.tenant_id == context.tenant_id
            )).first()

            if sku_db:
                sku_db.descripcion = str(row['descripcion'])
                sku_db.tiempo_ciclo_teorico = float(row['tiempo_ciclo_teorico'])
            else:
                nuevo_sku = MaestroSKU(
                    tenant_id=context.tenant_id,
                    codigo_sku=str(row['codigo_sku']),
                    descripcion=str(row['descripcion']),
                    tiempo_ciclo_teorico=float(row['tiempo_ciclo_teorico'])
                )
                db.add(nuevo_sku)
            skus_procesados += 1

        db.commit()
        return {"mensaje": f"Se procesaron {skus_procesados} SKUs correctamente."}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        # Fase K (auditoría QA #8): no se refleja el texto crudo de la
        # excepción (puede incluir detalles internos de pandas/DB); se
        # loguea completo del lado del servidor.
        db.rollback()
        logger.warning(f"Error procesando CSV de SKUs (tenant {context.tenant_id}): {e}")
        raise HTTPException(status_code=400, detail="Error procesando el archivo. Verificá el formato y los datos.")


@router.get("/erp/skus", response_model=List[MaestroSKU], tags=["Integración ERP"])
def listar_skus(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(MaestroSKU).where(MaestroSKU.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(MaestroSKU.activo == True)  # noqa: E712
    return db.exec(query).all()


@router.get("/erp/skus/{codigo_sku}", response_model=MaestroSKU, tags=["Integración ERP"])
def obtener_sku(
    codigo_sku: str,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    sku = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo_sku, MaestroSKU.tenant_id == context.tenant_id)).first()
    if not sku:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    return sku


@router.patch("/erp/skus/{codigo_sku}", response_model=MaestroSKU, tags=["Integración ERP"])
def actualizar_sku(
    codigo_sku: str,
    activo: Optional[bool] = None,
    descripcion: Optional[str] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    sku = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo_sku, MaestroSKU.tenant_id == context.tenant_id)).first()
    if not sku:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    if activo is not None:
        sku.activo = activo
    if descripcion is not None:
        sku.descripcion = descripcion
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


# ==========================================
# 📋 ABM DE ÓRDENES DE PRODUCCIÓN
# Antes no existía ningún CRUD vivo (el único código que las manejaba,
# app/routers/erp.py, no está registrado en main.py -- código muerto).
# id_orden sigue como PK legacy hasta la fase contract (C2).
# ==========================================
class OrdenProduccionCreate(BaseModel):
    id_orden: str
    sku_fk: Optional[str] = None
    linea_id: Optional[uuid.UUID] = None
    cantidad_esperada: int = 0
    plan_fecha: Optional[str] = None
    origen: str = "UI"

class OrdenProduccionUpdate(BaseModel):
    sku_fk: Optional[str] = None
    linea_id: Optional[uuid.UUID] = None
    cantidad_esperada: Optional[int] = None
    cantidad_producida: Optional[int] = None
    plan_fecha: Optional[str] = None
    estado: Optional[str] = None
    activo: Optional[bool] = None


@router.post("/ordenes/", response_model=OrdenProduccion, status_code=status.HTTP_201_CREATED)
def crear_orden(
    payload: OrdenProduccionCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    existente = db.exec(
        select(OrdenProduccion).where(
            OrdenProduccion.tenant_id == context.tenant_id,
            OrdenProduccion.id_orden == payload.id_orden,
            OrdenProduccion.activo == True,  # noqa: E712
        )
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail=f"Ya existe una orden activa con id_orden '{payload.id_orden}'.")

    if payload.sku_fk:
        sku = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == payload.sku_fk, MaestroSKU.tenant_id == context.tenant_id)).first()
        if not sku:
            raise HTTPException(status_code=400, detail="sku_fk no existe o pertenece a otra organización.")

    if payload.linea_id:
        linea = db.exec(select(Linea).where(Linea.id == payload.linea_id, Linea.tenant_id == context.tenant_id)).first()
        if not linea:
            raise HTTPException(status_code=400, detail="linea_id no existe o pertenece a otra organización.")

    nueva = OrdenProduccion(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva


@router.get("/ordenes/", response_model=List[OrdenProduccion])
def listar_ordenes(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(OrdenProduccion).where(OrdenProduccion.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(OrdenProduccion.activo == True)  # noqa: E712
    return db.exec(query).all()


@router.get("/ordenes/{id_orden}", response_model=OrdenProduccion)
def obtener_orden(
    id_orden: str,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    orden = db.exec(select(OrdenProduccion).where(OrdenProduccion.id_orden == id_orden, OrdenProduccion.tenant_id == context.tenant_id)).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")
    return orden


@router.patch("/ordenes/{id_orden}", response_model=OrdenProduccion)
def actualizar_orden(
    id_orden: str,
    payload: OrdenProduccionUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    orden = db.exec(select(OrdenProduccion).where(OrdenProduccion.id_orden == id_orden, OrdenProduccion.tenant_id == context.tenant_id)).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")

    datos = payload.model_dump(exclude_unset=True)
    if "linea_id" in datos and datos["linea_id"]:
        linea = db.exec(select(Linea).where(Linea.id == datos["linea_id"], Linea.tenant_id == context.tenant_id)).first()
        if not linea:
            raise HTTPException(status_code=400, detail="linea_id no existe o pertenece a otra organización.")
    if "sku_fk" in datos and datos["sku_fk"]:
        sku = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == datos["sku_fk"], MaestroSKU.tenant_id == context.tenant_id)).first()
        if not sku:
            raise HTTPException(status_code=400, detail="sku_fk no existe o pertenece a otra organización.")

    for key, value in datos.items():
        setattr(orden, key, value)
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden


@router.delete("/ordenes/{id_orden}")
def desactivar_orden(
    id_orden: str,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    orden = db.exec(select(OrdenProduccion).where(OrdenProduccion.id_orden == id_orden, OrdenProduccion.tenant_id == context.tenant_id)).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")
    orden.activo = False
    db.add(orden)
    db.commit()
    return {"mensaje": "Orden desactivada."}


# ==========================================
# 🏗️ ABM DE MÁQUINAS (Fase G)
# El ingreso de eventos (scans.py) ya acepta y valida maquina_id contra
# MaquinaEstacion desde Fase E1; hasta ahora no existía ningún CRUD para
# darlas de alta ni asociarlas a una estación.
# ==========================================
@router.post("/maquinas/", response_model=Maquina, status_code=status.HTTP_201_CREATED)
def crear_maquina(
    payload: MaquinaCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    existente = db.exec(
        select(Maquina).where(
            Maquina.tenant_id == context.tenant_id,
            Maquina.codigo_externo == payload.codigo_externo,
            Maquina.activo == True,  # noqa: E712
        )
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail=f"Ya existe una máquina activa con código '{payload.codigo_externo}'.")

    nueva = Maquina(tenant_id=context.tenant_id, **payload.model_dump())
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva


@router.get("/maquinas/", response_model=List[Maquina])
def listar_maquinas(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    _requerir_permiso_inactivos(incluir_inactivos, usuario_actual)
    query = select(Maquina).where(Maquina.tenant_id == context.tenant_id)
    if not incluir_inactivos:
        query = query.where(Maquina.activo == True)  # noqa: E712
    return db.exec(query).all()


@router.get("/maquinas/{maquina_id}", response_model=Maquina)
def obtener_maquina(
    maquina_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    maquina = db.exec(select(Maquina).where(Maquina.id == maquina_id, Maquina.tenant_id == context.tenant_id)).first()
    if not maquina:
        raise HTTPException(status_code=404, detail="Máquina no encontrada.")
    return maquina


@router.patch("/maquinas/{maquina_id}", response_model=Maquina)
def actualizar_maquina(
    maquina_id: uuid.UUID,
    payload: MaquinaUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    maquina = db.exec(select(Maquina).where(Maquina.id == maquina_id, Maquina.tenant_id == context.tenant_id)).first()
    if not maquina:
        raise HTTPException(status_code=404, detail="Máquina no encontrada.")
    datos = payload.model_dump(exclude_unset=True)
    for key, value in datos.items():
        setattr(maquina, key, value)
    db.add(maquina)
    db.commit()
    db.refresh(maquina)
    return maquina


@router.delete("/maquinas/{maquina_id}")
def desactivar_maquina(
    maquina_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    maquina = db.exec(select(Maquina).where(Maquina.id == maquina_id, Maquina.tenant_id == context.tenant_id)).first()
    if not maquina:
        raise HTTPException(status_code=404, detail="Máquina no encontrada.")
    maquina.activo = False
    db.add(maquina)
    db.commit()
    return {"mensaje": "Máquina desactivada."}


@router.post("/maquinas/{maquina_id}/estaciones", response_model=MaquinaEstacion, status_code=status.HTTP_201_CREATED)
def asociar_maquina_a_estacion(
    maquina_id: uuid.UUID,
    payload: MaquinaEstacionCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    maquina = db.exec(select(Maquina).where(Maquina.id == maquina_id, Maquina.tenant_id == context.tenant_id)).first()
    if not maquina:
        raise HTTPException(status_code=404, detail="Máquina no encontrada.")
    estacion = db.exec(select(Estacion).where(Estacion.id == payload.estacion_id, Estacion.tenant_id == context.tenant_id)).first()
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada en este tenant.")

    existente = db.exec(
        select(MaquinaEstacion).where(
            MaquinaEstacion.maquina_id == maquina_id,
            MaquinaEstacion.estacion_id == payload.estacion_id,
            MaquinaEstacion.activo == True,  # noqa: E712
        )
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail="Esa máquina ya está asociada a esa estación.")

    nueva = MaquinaEstacion(tenant_id=context.tenant_id, maquina_id=maquina_id, estacion_id=payload.estacion_id)
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva


@router.get("/maquinas/{maquina_id}/estaciones", response_model=List[MaquinaEstacion])
def listar_asociaciones_maquina(
    maquina_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    maquina = db.exec(select(Maquina).where(Maquina.id == maquina_id, Maquina.tenant_id == context.tenant_id)).first()
    if not maquina:
        raise HTTPException(status_code=404, detail="Máquina no encontrada.")
    return db.exec(
        select(MaquinaEstacion).where(
            MaquinaEstacion.maquina_id == maquina_id,
            MaquinaEstacion.tenant_id == context.tenant_id,
            MaquinaEstacion.activo == True,  # noqa: E712
        )
    ).all()


@router.delete("/maquinas/{maquina_id}/estaciones/{asociacion_id}")
def quitar_asociacion_maquina(
    maquina_id: uuid.UUID,
    asociacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia),
):
    asociacion = db.exec(
        select(MaquinaEstacion).where(
            MaquinaEstacion.id == asociacion_id,
            MaquinaEstacion.maquina_id == maquina_id,
            MaquinaEstacion.tenant_id == context.tenant_id,
        )
    ).first()
    if not asociacion:
        raise HTTPException(status_code=404, detail="Asociación no encontrada.")
    asociacion.activo = False
    db.add(asociacion)
    db.commit()
    return {"mensaje": "Asociación desactivada."}

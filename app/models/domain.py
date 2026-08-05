import uuid
from datetime import datetime, time, date
from enum import Enum
from typing import List, Optional
from sqlmodel import SQLModel, Field, Relationship, Index
from sqlalchemy import Column
from sqlalchemy import Enum as SaEnum

# ==========================================
# 1. ENUMS (Lógica de Negocio OS Shell & B2B)
# ==========================================
class TipoParada(str, Enum):
    PLANIFICADA = "planificada"
    NO_PLANIFICADA = "no_planificada"

class EstadoOrden(str, Enum):
    ABIERTA = "abierta"
    EN_PROGRESO = "en_progreso"
    CERRADA = "cerrada"

class EstadoParada(str, Enum):
    PENDIENTE = "pendiente"       # Gap detectado automáticamente, esperando al supervisor
    CLASIFICADA = "clasificada"   # El supervisor ya le asignó un motivo

class RolUsuario(str, Enum):
    SUPERADMIN = "superadmin"
    GERENCIA = "gerencia"
    PRODUCCION = "produccion"
    SUPERVISOR = "supervisor"
    OPERARIO = "operario"

class CategoriaParada(str, Enum):
    NORMAL = "normal"
    MICRO_MENOR = "micro_parada_menor"
    MICRO_MAYOR = "micro_parada_mayor"

class TipoProduccion(str, Enum):
    DISCRETA = "discreta"      # Springwall: 1 ping = 1 unidad.
    POR_LOTES = "por_lotes"    # Green Mills: 1 ping = X unidades.

# Nuevos Enums obligatorios por OS Shell
class TipoTenant(str, Enum):
    EMPRESA = "empresa"
    PLANTA = "planta"

class ModoAsignacionOperarios(str, Enum):
    MANUAL = "manual"
    ESCANEO = "escaneo"

class ModoAsignacionOperariosEstacion(str, Enum):
    HEREDAR = "heredar"
    MANUAL = "manual"
    ESCANEO = "escaneo"

# Enums de hardening production-grade (HANDOFF_STG_PRODUCTION_GRADE.md)
class EstadoTenant(str, Enum):
    ACTIVO = "activo"
    UI_SUSPENDIDA = "ui_suspendida"
    SUSPENSION_TOTAL = "suspension_total"

class MetodoCalidadLinea(str, Enum):
    POR_TIEMPO = "por_tiempo"
    POR_RECHAZO = "por_rechazo"

# ==========================================
# 2. MIXIN B2B MULTI-TENANT
# ==========================================
class TenantBase(SQLModel):
    """Garantiza el aislamiento B2B. Todas las tablas lo heredan."""
    tenant_id: str = Field(index=True, description="ID del cliente/tenant")

class Tenant(SQLModel, table=True):
    __tablename__ = "tenants_saas"
    
    id: str = Field(primary_key=True)
    nombre: str = Field(description="Nombre comercial")
    
    # 🟢 Forzamos a SQLAlchemy a enviar "empresa" en minúsculas a Postgres
    tipo: TipoTenant = Field(
        default=TipoTenant.EMPRESA,
        sa_column=Column(SaEnum(TipoTenant, values_callable=lambda obj: [e.value for e in obj]))
    )
    parent_id: Optional[str] = Field(default=None, foreign_key="tenants_saas.id")
    modulos_contratados: str = Field(default="tymeo")
    theme_default: Optional[str] = None
    logo_url: Optional[str] = None
    color_primario: Optional[str] = None
    locale_default: str = Field(default="es")
    
    # 🟢 Mismo fix para la asignación
    modo_asignacion_operarios: ModoAsignacionOperarios = Field(
        default=ModoAsignacionOperarios.MANUAL,
        sa_column=Column(SaEnum(ModoAsignacionOperarios, values_callable=lambda obj: [e.value for e in obj]))
    )
    activo: bool = Field(default=True)
    tolerancia_lento_pct: float = Field(default=1.15)
    tolerancia_alerta_pct: float = Field(default=1.25)
    # Fase N: objetivo de OEE de referencia del tenant, configurable por
    # Gerencia (antes estaba hardcodeado en el front: 75 en la tendencia,
    # 85 en el Command Center -- ninguno de los dos venía de acá).
    oee_objetivo_pct: float = Field(default=85.0)
    regex_parser_orden: Optional[str] = None
    regex_parser_sku: Optional[str] = None
    origen_maestros: str = Field(default="MANUAL")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Suspensión de tenant (Fase D). Sólo SuperAdmin puede cambiarlo.
    estado: EstadoTenant = Field(
        default=EstadoTenant.ACTIVO,
        sa_column=Column(SaEnum(EstadoTenant, values_callable=lambda obj: [e.value for e in obj]))
    )

# ==========================================
# 2.5 ACCESO SAAS (Usuarios B2B)
# ==========================================
class UsuarioSaaS(SQLModel, table=True):
    __tablename__ = "usuarios_saas"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    auth0_id: str = Field(unique=True, index=True, description="ID exacto que viene del token de Auth0 (sub)")
    tenant_id: str = Field(index=True, foreign_key="tenants_saas.id", description="Ej: springwall, tyme_core")
    email: Optional[str] = Field(default=None, description="Email del usuario")
    rol: RolUsuario = Field(default=RolUsuario.SUPERVISOR)
    activo: bool = Field(default=True)
    nombre: Optional[str] = Field(default=None, description="Nombre del usuario")
    apellido: Optional[str] = Field(default=None, description="Apellido del usuario")

# ==========================================
# 3. PLANTA FÍSICA Y PERSONAL (Jerarquía Expandida)
# ==========================================
class Planta(TenantBase, table=True):
    __tablename__ = "plantas"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    nombre: str = Field(description="Ej: Planta Garín, Planta Pilar")
    ubicacion: Optional[str] = None
    timezone: str = Field(default="America/Buenos_Aires", description="Timezone IANA de la planta")
    activo: bool = Field(default=True)

class Linea(TenantBase, table=True):
    __tablename__ = "dim_lineas"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # NOT NULL: la migración C1 sanea huérfanos con "Planta Default" antes de imponerlo.
    planta_id: uuid.UUID = Field(foreign_key="plantas.id")
    nombre: str

    # Tipado estricto OS Shell
    # (values_callable: el tipo Postgres 'modoasignacionoperarios' guarda valores en
    # minúscula; sin esto SQLAlchemy manda el .name del enum y falla el INSERT)
    modo_asignacion_operarios: ModoAsignacionOperarios = Field(
        default=ModoAsignacionOperarios.MANUAL,
        sa_column=Column(SaEnum(ModoAsignacionOperarios, values_callable=lambda obj: [e.value for e in obj]))
    )
    tipo_produccion: TipoProduccion = Field(default=TipoProduccion.DISCRETA)
    metodo_calidad: MetodoCalidadLinea = Field(default=MetodoCalidadLinea.POR_RECHAZO)
    activo: bool = Field(default=True)

class Supervisor(TenantBase, table=True):
    __tablename__ = "dim_supervisores"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    legajo: str = Field(index=True)
    nombre_completo: str
    activo: bool = Field(default=True)

class Estacion(TenantBase, table=True):
    __tablename__ = "dim_estaciones"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    nombre: str
    tipo: str  # Ej: "sensor", "escaneo_manual", "calidad"
    
    umbral_optimo: int = Field(default=240, description="Tiempo ideal en segundos")
    umbral_lento: int = Field(default=280, description="Límite de tiempo aceptable")
    umbral_alerta: int = Field(default=300, description="Tiempo que dispara alerta")
    
    activa: bool = Field(default=True, description="Apagar si hoy no se usa")
    posicion_linea: int = Field(default=1, description="Secuencia lógica (1,2,3...)")
    ramal: str = Field(default="Principal", description="Ej: Principal, Ramal A, Ramal B")
    
    parent_id: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_estaciones.id")
    # NOT NULL: la migración C1 sanea huérfanos con "Línea Default" antes de imponerlo.
    linea_id: uuid.UUID = Field(foreign_key="dim_lineas.id")

    codigo_plc: Optional[str] = Field(default=None, index=True)
    
    # Tipado estricto con soporte de Herencia OS Shell
    # (values_callable: mismo motivo que en Linea.modo_asignacion_operarios)
    modo_asignacion_operarios: ModoAsignacionOperariosEstacion = Field(
        default=ModoAsignacionOperariosEstacion.HEREDAR,
        sa_column=Column(SaEnum(ModoAsignacionOperariosEstacion, values_callable=lambda obj: [e.value for e in obj]))
    )

    # Estado en Vivo
    orden_activa_fk: Optional[str] = Field(default=None, description="La OP que se está corriendo ahora")
    sku_activo_fk: Optional[str] = Field(default=None, description="El SKU que se está corriendo ahora")

class Operario(TenantBase, table=True):
    __tablename__ = "dim_operarios"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    legajo: str = Field(index=True)
    nombre_completo: str
    activo: bool = Field(default=True)

class Turno(TenantBase, table=True):
    __tablename__ = "dim_turnos"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    nombre: str
    hora_inicio: time
    hora_fin: time
    descanso_minutos: int = Field(default=0, description="Minutos a descontar de la Disponibilidad")
    linea_id: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_lineas.id")
    activo: bool = Field(default=True)

class AsignacionTurno(TenantBase, table=True):
    __tablename__ = "asignaciones_turno"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    fecha: date = Field(default_factory=datetime.today)
    estacion_fk: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    operario_fk: uuid.UUID = Field(foreign_key="dim_operarios.id")
    turno_fk: uuid.UUID = Field(foreign_key="dim_turnos.id")

class AsignacionSupervisor(TenantBase, table=True):
    """Tablero de supervisión diaria (Fase H). El turno es una plantilla
    maestra; el supervisor a cargo se registra por día, no como atributo
    fijo del turno. Idempotente por (tenant_id, fecha, linea_id, turno_id):
    reasignar sobrescribe (upsert), nunca duplica."""
    __tablename__ = "asignaciones_supervisor"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    fecha: date
    linea_id: uuid.UUID = Field(foreign_key="dim_lineas.id")
    turno_id: uuid.UUID = Field(foreign_key="dim_turnos.id")
    supervisor_id: uuid.UUID = Field(foreign_key="dim_supervisores.id")

# ==========================================
# 4. CATÁLOGO Y ÓRDENES (Input del ERP / Excel)
# ==========================================
class MaestroSKU(TenantBase, table=True):
    __tablename__ = "maestro_skus"
    # NOTA (C1/C2): codigo_sku sigue siendo PK legacy durante la fase expand.
    # 'id' es la nueva identidad interna UUID; las FKs nuevas deben apuntar a
    # 'id', no a codigo_sku. codigo_sku deja de ser PK recién en la fase contract (C2).
    codigo_sku: str = Field(primary_key=True, description="El código real del ERP")
    id: uuid.UUID = Field(default_factory=uuid.uuid4, index=True, unique=True, description="Identidad interna UUID (reemplaza a codigo_sku como PK en C2)")
    descripcion: str
    modelo: Optional[str] = None
    medida: Optional[str] = None

    tiempo_ciclo_teorico: float = Field(default=240.0, description="Segundos ideales por unidad")
    umbral_calidad: float = Field(default=1800.0, description="Tolerancia en estación de calidad")

    linea_id: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_lineas.id") # OS Shell requirement para uploads
    unidades_por_ciclo: int = Field(default=1, description="Factor de lote (ej: 4 panes por molde)")
    activo: bool = Field(default=True)

class OrdenProduccion(TenantBase, table=True):
    __tablename__ = "ordenes_produccion"
    # NOTA (C1/C2): id_orden sigue siendo PK legacy durante la fase expand.
    # 'id' es la nueva identidad interna UUID; las FKs nuevas deben apuntar a
    # 'id', no a id_orden. id_orden deja de ser PK recién en la fase contract (C2).
    id_orden: str = Field(primary_key=True, description="Número de OP del ERP")
    id: uuid.UUID = Field(default_factory=uuid.uuid4, index=True, unique=True, description="Identidad interna UUID (reemplaza a id_orden como PK en C2)")

    sku_fk: Optional[str] = Field(default=None, foreign_key="maestro_skus.codigo_sku")
    linea_id: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_lineas.id")

    cantidad_esperada: int = Field(default=0)
    cantidad_producida: int = Field(default=0)

    plan_fecha: Optional[str] = Field(default=None, description="Ej: YYYY-MM-DD")
    estado: EstadoOrden = Field(default=EstadoOrden.ABIERTA)
    origen: str = Field(default="UI")
    activo: bool = Field(default=True)

class MotivoParada(TenantBase, table=True):
    __tablename__ = "dim_motivos_parada"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    nombre: str
    tipo_parada: TipoParada
    activo: bool = Field(default=True)

# ==========================================
# 4.5 MÁQUINAS, DISPOSITIVOS M2M Y ALCANCE POR PLANTA
# ==========================================
class Maquina(TenantBase, table=True):
    __tablename__ = "dim_maquinas"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    codigo_externo: str = Field(index=True, description="Identificador físico del equipo")
    nombre: Optional[str] = None
    activo: bool = Field(default=True)

class MaquinaEstacion(TenantBase, table=True):
    """Asociación N:N tenant-aware entre Maquina y Estacion."""
    __tablename__ = "maquina_estacion"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    maquina_id: uuid.UUID = Field(foreign_key="dim_maquinas.id")
    estacion_id: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    activo: bool = Field(default=True)

class UsuarioPlanta(TenantBase, table=True):
    """Alcance geolocalizado: aplica sólo a roles SUPERVISOR y OPERARIO (Fase D)."""
    __tablename__ = "usuario_planta"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    usuario_id: uuid.UUID = Field(foreign_key="usuarios_saas.id")
    planta_id: uuid.UUID = Field(foreign_key="plantas.id")
    activo: bool = Field(default=True)

class ModuloPermiso(TenantBase, table=True):
    """Permiso por módulo y planta (Fase F, InspectIA OS).

    SUPERADMIN/GERENCIA/PRODUCCION ven todos los módulos contratados por el
    tenant sin necesitar filas acá (igual que ya pasa con UsuarioPlanta); esta
    tabla sólo registra asignaciones explícitas para SUPERVISOR/OPERARIO,
    scopeadas a una planta puntual. Hoy el único módulo real es "tymeo"; las
    demás claves (oee-hub, vision, logistica, seguridad) quedan listas para
    cuando existan esos backends.
    """
    __tablename__ = "modulo_permiso"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    usuario_id: uuid.UUID = Field(foreign_key="usuarios_saas.id")
    modulo: str = Field(index=True, description="Ej: tymeo, oee-hub, vision, logistica, seguridad")
    planta_id: uuid.UUID = Field(foreign_key="plantas.id")
    rol: RolUsuario
    activo: bool = Field(default=True)

class ApiKeyDispositivo(TenantBase, table=True):
    """Credencial M2M para hardware fijo. Formato entregado al cliente: key_id.secret."""
    __tablename__ = "api_keys_dispositivo"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    key_id: str = Field(index=True, unique=True, description="Parte pública/indexada de la key")
    secret_hash: str = Field(description="Hash bcrypt del secret; el secret nunca se persiste en claro")
    estacion_id: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    activo: bool = Field(default=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    revoked_at: Optional[datetime] = Field(default=None)

# ==========================================
# 5. TRANSACCIONES LEGACY (Retenidas por retrocompatibilidad)
# ==========================================
class EventoEscaneo(TenantBase, table=True):
    """(Legacy) Endpoint original de Springwall."""
    __tablename__ = "eventos_escaneo"
    __table_args__ = (Index("ix_tenant_barcode", "tenant_id", "barcode"),)
    
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    barcode: str = Field(description="El código completo de 25 caracteres")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    estacion_fk: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    orden_fk: Optional[str] = Field(default=None, foreign_key="ordenes_produccion.id_orden")
    operario_fk: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_operarios.id")
    
    desempeno: Optional[str] = Field(default=None, description="OPTIMO, LENTO o ALERTA")
    segundos_proceso: Optional[int] = Field(default=None)
    es_retrabajo: bool = Field(default=False)
    
class ParadaDetectada(TenantBase, table=True):
    __tablename__ = "paradas_detectadas"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    estacion_fk: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    inicio: datetime
    fin: Optional[datetime] = None
    duracion_segundos: Optional[float] = None
    estado: EstadoParada = Field(default=EstadoParada.PENDIENTE)
    motivo_fk: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_motivos_parada.id")
    # Fase N: distingue paradas detectadas automáticamente por gap de scans
    # (AUTOMATICA, el flujo pendiente->clasificar) de las cargadas de
    # antemano por un supervisor (PLANIFICADA, vía /paradas/planificadas).
    # Necesario para separar "historial de clasificadas" de "programadas"
    # en el front sin ambigüedad -- antes ambas terminaban con el mismo
    # estado=CLASIFICADA y no había forma de diferenciarlas.
    origen: str = Field(default="AUTOMATICA")

class CicloProduccion(TenantBase, table=True):
    """(Legacy) Endpoint original de PLC ciego."""
    __tablename__ = "eventos_plc_ciclos"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    maquina_id: str = Field(index=True, description="Identificador del equipo (ej. armadora_1)")
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    formato_moldes: int = Field(description="Formato de la bandeja (4 o 5)")
    panes_producidos: int = Field(description="Cantidad de unidades producidas en el ciclo")
    delta_tiempo_s: float = Field(description="Segundos transcurridos desde el ciclo anterior")
    categoria_parada: CategoriaParada = Field(index=True)

# ==========================================
# 6. MOTOR TRANSACCIONAL B2B (OEE LITE UNIVERSAL)
# ==========================================
class LiteEventoProduccion(TenantBase, table=True):
    """
    Tabla universal de eventos de alta velocidad para OEE Lite.
    """
    __tablename__ = "lite_eventos_produccion"
    
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    
    # Datos de Origen (Edge)
    id_estacion: str = Field(index=True, description="Código de la máquina/estación")
    codigo_pieza: Optional[str] = Field(default=None, index=True, description="Null si es sensor fotoeléctrico")
    
    # Anclas de Trazabilidad Universal
    orden_fk: Optional[str] = Field(default=None, index=True)
    cantidad_producida: int = Field(default=1)
    
    # Impacto Green Mills: Snapshot Inmutable del Factor de Lote
    unidades_procesadas: int = Field(default=1, description="Factor de lote al momento exacto del escaneo")
    
    # Timeline y Analítica
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    delta_t_segundos: float = Field(default=0.0)
    estado: str = Field(default="PENDIENTE", index=True) # OPTIMO, LENTO, ALERTA, PARADA
    es_parada_detectada: bool = Field(default=False)
    tiempo_perdido_segundos: float = Field(default=0.0)

    # Máquina física (opcional; NULL si el hardware informa una máquina
    # no asociada a la estación — se acepta el evento igual, ver Fase E1).
    maquina_id: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_maquinas.id")

    # Calidad por rechazo (reemplaza a es_retrabajo de EventoEscaneo).
    unidades_rechazadas: int = Field(default=0, description="0 <= unidades_rechazadas <= unidades_procesadas")

    # Idempotencia Edge (Fase E1).
    event_id: Optional[uuid.UUID] = Field(default=None, index=True, unique=True, description="UUIDv4 estable enviado por el productor")
    payload_hash: Optional[str] = Field(default=None, description="Hash canónico del payload relevante")

    # Snapshot inmutable: incluido_oee = estacion.activa al momento del evento.
    # No se recalcula si la estación cambia de estado después.
    incluido_oee: bool = Field(default=True)

    # Snapshot inmutable (Fase E2): tiempo ideal POR UNIDAD usado al momento
    # del escaneo (umbral del SKU activo si había uno, si no el de la
    # estación). Necesario porque estacion.sku_activo_fk es mutable y no
    # podemos saber retroactivamente qué SKU corría al momento de un evento
    # pasado. tiempo_ideal_evento = tiempo_ideal_seg * unidades_procesadas.
    tiempo_ideal_seg: float = Field(default=0.0)
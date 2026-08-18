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

class EstadoPlan(str, Enum):
    """Fase AA (pedido de Green Mills): un Plan agrupa las Ordenes que se
    van a producir en un día -- sin fecha_fin (a pedido explícito, ver
    PlanProduccion.fecha_inicio); se cierra solo cuando se agota la
    secuencia de órdenes (ver avanzar_orden en operacion.py).

    QA-01 (auditoría QA post-Fase AG): el modelo anterior (ABIERTO/
    CERRADO) no tenía invariante de unicidad -- podían coexistir varios
    planes ABIERTO en la misma línea, y resolver_orden_activa
    (clasificacion.py) tomaba cualquiera con `.first()` (orden no
    determinístico de Postgres), contaminando qué orden/SKU/perfil de
    tiempos se le atribuía a cada scan. Rediseño (decisión del usuario):
    BORRADOR/PROGRAMADO no compiten por la línea -- ninguno participa en
    resolver_orden_activa. EN_PROGRESO es el único estado operativo,
    garantizado único por (tenant_id, linea_id) en DOS capas: a nivel de
    aplicación (crear_plan/activar_plan en configuracion.py) Y a nivel
    de base de datos (índice único parcial, ver migración
    plan_estados_qa01) -- la invariante no depende sólo del
    procedimiento humano, tal como pide la conclusión de la auditoría.
    CANCELADO es nuevo: distingue un plan abortado a mitad de camino de
    uno CERRADO normalmente (agotó su secuencia de órdenes) -- resuelve
    también QA-13 (desactivar un plan no limpiaba su orden activa)."""
    BORRADOR = "borrador"
    PROGRAMADO = "programado"
    EN_PROGRESO = "en_progreso"
    CERRADO = "cerrado"
    CANCELADO = "cancelado"

class EstadoParada(str, Enum):
    PENDIENTE = "pendiente"       # Gap detectado automáticamente, esperando al supervisor
    CLASIFICADA = "clasificada"   # El supervisor ya le asignó un motivo
    # Fase DU (auditoría de backend, P0-05 revisado): sólo para origen=
    # PLANIFICADA que todavía no empezó -- reemplaza el hard-delete de
    # eliminar_parada_planificada por soft-delete, consistente con el
    # resto del sistema ("no eliminar ni ocultar", ver comentario de
    # recomputo.py). Nunca se usa para AUTOMATICA ni para una PLANIFICADA
    # ya en curso/pasada -- ésas nunca fueron ni son borrables.
    ANULADA = "anulada"


class EstadoExclusionOee(str, Enum):
    """Fase CC (FE-P0-08, auditoría de robustez, batch 3): workflow de
    falso positivo -- una parada detectada automáticamente que en
    realidad NO representa una pérdida real (glitch de sensor, corte de
    red que generó un hueco artificial, etc.). Quien clasifica paradas
    (Encargado/Supervisor/Gerencia/SuperAdmin, mismo gate que
    clasificar_parada) puede PROPONER excluirla de los cálculos de OEE;
    un segundo usuario -- Gerencia o SuperAdmin, distinto de quien
    propuso -- tiene que aprobarla o rechazarla antes de que afecte el
    cálculo (ver operacion.py, analytics.py::_calcular_metricas_oee).
    Ninguna parada se borra ni se oculta -- el historial (/supervisor/
    paradas) sigue mostrando TODAS, con su estado de exclusión, para
    auditoría."""
    NINGUNA = "ninguna"       # No propuesta -- cuenta normal en OEE (default)
    PROPUESTA = "propuesta"   # Esperando resolución de un segundo usuario
    APROBADA = "aprobada"     # Confirmada como falso positivo -- EXCLUIDA de OEE
    RECHAZADA = "rechazada"   # El segundo usuario la revisó y la confirmó como real -- cuenta en OEE

class RolUsuario(str, Enum):
    SUPERADMIN = "superadmin"
    GERENCIA = "gerencia"
    PRODUCCION = "produccion"
    SUPERVISOR = "supervisor"
    # Pedido explícito del usuario: rol intermedio entre Supervisor y
    # Operario -- el supervisor a veces está ocupado con otras tareas y no
    # puede estar atento a clasificar paradas. Encargado tiene cuenta web
    # (a diferencia de Operario) pero acceso deliberadamente angosto: sólo
    # paradas pendientes/clasificar/historial (ver ROLES_SUPERVISION_COMPLETA
    # en operacion.py, que lo excluye explícitamente del resto -- dotación,
    # asignación de supervisores, avanzar Plan, paradas planificadas).
    ENCARGADO = "encargado"
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
    # Fase AC (rediseño de umbrales/tolerancias, pedido de Green Mills):
    # Empresa deja de ser un nivel de la cascada de tiempos -- no aportaba
    # nada real (un único valor de tolerancia % para TODO el tenant, sin
    # relación con el ritmo real de cada línea). El piso ahora es Línea,
    # siempre en segundos absolutos (ver Linea.tiempo_ideal_seg y
    # clasificacion.py). tolerancia_lento_pct/alerta_pct quedan retirados.
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

    # Fase AC (rediseño de umbrales/tolerancias, pedido de Green Mills):
    # reemplaza los 5 campos viejos (umbral_optimo/lento/alerta en
    # segundos + tolerancia_lento/alerta_pct en %, Fases Q/R) por UN solo
    # perfil de 3 tiempos, siempre en segundos, nunca porcentaje. Línea es
    # ahora el ÚNICO piso de la cascada (ya no hay Estación ni Empresa/
    # Tenant como niveles intermedios/default -- ver clasificacion.py):
    # se usa siempre que un evento NO resuelve SKU, o resuelve un SKU
    # cuyo perfil está incompleto (falta lento y/o alerta, ver
    # MaestroSKU). NOT NULL con default 240/280/300 (mismos valores que
    # antes eran la constante hardcodeada de sistema): así Línea siempre
    # tiene un piso funcionando desde que se crea, sin bloquear el alta,
    # y esos 3 números dejan de estar hardcodeados en el código -- son
    # datos editables por línea desde el minuto uno.
    tiempo_ideal_seg: float = Field(default=240.0, description="Piso de línea: segundos ideales por ciclo cuando no hay SKU (o su perfil está incompleto)")
    tiempo_lento_seg: float = Field(default=280.0, description="Piso de línea: segundos que clasifican LENTO")
    tiempo_alerta_seg: float = Field(default=300.0, description="Piso de línea: segundos que disparan ALERTA/parada")

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

    # Fase AC (rediseño de umbrales/tolerancias): Estación deja de ser un
    # nivel de la cascada -- ya no tiene umbral_optimo/lento/alerta ni
    # tolerancia_lento/alerta_pct propios (eran 5 campos redundantes con
    # los de Línea, y con las % encima, la fuente real del problema que
    # pidió simplificar Green Mills). Una estación sigue pudiendo tener
    # tiempos propios, pero SIEMPRE en combinación con un SKU puntual --
    # ver SkuTiempoEstacion (override por SKU×Estación) y
    # clasificacion.resolver_umbrales_evento. Sin SKU resuelto, TODAS las
    # estaciones de una línea comparten el piso de Linea.tiempo_ideal_seg.

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
    # Fase Q (feedback de producto): hay empresas Lu-Vi, Lu-Sa, Lu-Do, y
    # combinaciones más raras (ej. sólo fines de semana). CSV de días ISO
    # (1=lunes...7=domingo, ver datetime.isoweekday()). Default = los 7
    # días: preserva el comportamiento de todos los turnos existentes
    # (aplicaban todos los días, sin excepción) sin necesitar backfill.
    dias_semana: str = Field(default="1,2,3,4,5,6,7", description="CSV de días ISO en que aplica este turno (1=lunes..7=domingo)")

class AsignacionTurno(TenantBase, table=True):
    __tablename__ = "asignaciones_turno"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    fecha: date = Field(default_factory=datetime.today)
    estacion_fk: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    operario_fk: uuid.UUID = Field(foreign_key="dim_operarios.id")
    turno_fk: uuid.UUID = Field(foreign_key="dim_turnos.id")
    # Fase BZ (auditoría de robustez): antes escanear [SALIR] en la
    # terminal sólo reseteaba estado local del front -- ningún registro
    # quedaba del lado del backend. Puramente informativo/auditoría: NO
    # cambia cómo se resuelve el operario de un evento (eso sigue siendo
    # por estación+turno+fecha, ver login_operario_terminal en
    # scans.py) -- formalizar esa resolución por ventana horaria real
    # sería un cambio de modelo más grande, fuera de este alcance.
    hora_salida: Optional[datetime] = Field(default=None)

class AsignacionSupervisor(TenantBase, table=True):
    """Regla de supervisión programable (Fase Q -- reemplaza el tablero
    diario de Fase H). El turno es una plantilla maestra; el supervisor a
    cargo se define como una REGLA recurrente (qué días de la semana, con
    vigencia desde/hasta), no un registro por cada día exacto. Pueden
    coexistir varias reglas para la misma (línea, turno) a lo largo del
    tiempo (ej. Juan cubre Ene-Jun, Pedro cubre Jul en adelante) -- la
    resolución de "quién está a cargo hoy" toma la regla vigente con
    vigencia_desde más reciente entre las que matchean el día."""
    __tablename__ = "asignaciones_supervisor"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    linea_id: uuid.UUID = Field(foreign_key="dim_lineas.id")
    turno_id: uuid.UUID = Field(foreign_key="dim_turnos.id")
    supervisor_id: uuid.UUID = Field(foreign_key="dim_supervisores.id")
    dias_semana: str = Field(description="CSV de días ISO en que aplica esta regla (1=lunes..7=domingo)")
    vigencia_desde: date
    vigencia_hasta: Optional[date] = Field(default=None, description="NULL = sigue vigente indefinidamente")

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

    # Fase AC (rediseño de umbrales/tolerancias): tiempo_ciclo_teorico se
    # renombra a tiempo_ideal_seg -- mismo campo, mismo default, nombre
    # consistente con Linea.tiempo_ideal_seg y SkuTiempoEstacion.tiempo_ideal_seg
    # (los 3 niveles de la cascada usan exactamente los mismos 3 nombres
    # de campo). tiempo_lento_seg/tiempo_alerta_seg son NUEVOS y
    # nullable a propósito: un SKU siempre existe con su tiempo ideal
    # (tiene default), pero recién tiene un perfil de clasificación
    # PROPIO cuando alguien carga los 3. Si falta cualquiera de los dos
    # (no sólo si faltan ambos), el perfil se considera incompleto y el
    # evento cae ENTERO al piso de Línea -- nunca se mezclan campos de
    # dos fuentes distintas para un mismo evento (ver resolver_umbrales_evento).
    tiempo_ideal_seg: float = Field(default=240.0, description="Segundos ideales por unidad")
    tiempo_lento_seg: Optional[float] = Field(default=None, description="Segundos que clasifican LENTO para este SKU (NULL = perfil incompleto, cae al piso de línea)")
    tiempo_alerta_seg: Optional[float] = Field(default=None, description="Segundos que disparan ALERTA para este SKU (NULL = perfil incompleto, cae al piso de línea)")
    umbral_calidad: float = Field(default=1800.0, description="Tolerancia en estación de calidad -- concepto de CALIDAD, no de rendimiento; no forma parte del perfil de tiempos de arriba")

    linea_id: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_lineas.id") # OS Shell requirement para uploads
    unidades_por_ciclo: int = Field(default=1, description="Factor de lote (ej: 4 panes por molde)")
    activo: bool = Field(default=True)

class SkuTiempoEstacion(TenantBase, table=True):
    """Fase R (feedback de producto): un mismo SKU puede tardar distinto
    según en qué estación se procesa (ej. la Armadora vs. el Embalaje) --
    MaestroSKU.tiempo_ideal_seg es un único valor genérico por SKU, no
    alcanza para eso. Esta tabla es un OVERRIDE opcional por
    (SKU, Estación): si no existe fila acá para el par, scans.py cae al
    perfil genérico del SKU (o al piso de línea si ese también está
    incompleto -- no bloquea nada, sólo hace falta cargar el override
    donde realmente difiera del genérico).

    Fase AC: a diferencia de MaestroSKU, acá los 3 campos son NOT NULL --
    una fila de override, si existe, siempre está completa (no tiene
    sentido un override parcial: quien crea la fila define un perfil
    entero para ese SKU en esa estación puntual, o no la crea)."""
    __tablename__ = "sku_tiempo_estacion"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    sku_fk: str = Field(foreign_key="maestro_skus.codigo_sku")
    estacion_id: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    tiempo_ideal_seg: float = Field(description="Segundos ideales por unidad de este SKU en esta estación")
    tiempo_lento_seg: float = Field(description="Segundos que clasifican LENTO para este SKU en esta estación")
    tiempo_alerta_seg: float = Field(description="Segundos que disparan ALERTA para este SKU en esta estación")
    activo: bool = Field(default=True)

    __table_args__ = (
        Index("ix_sku_tiempo_estacion_unico", "tenant_id", "sku_fk", "estacion_id", unique=True),
    )

class PlanProduccion(TenantBase, table=True):
    """Fase AA (pedido de Green Mills, reunión de producción): un nivel
    nuevo arriba de Orden -- "el plan contiene todo lo que se va a
    producir ese día". Agrupa Ordenes (que se asocian a un SKU y una
    cantidad cada una, ver OrdenProduccion.plan_id/secuencia); dentro de
    un plan se pueden producir cantidades de distintos SKUs, uno por
    orden, en secuencia. Deliberadamente SIN fecha_fin -- el plan no
    tiene un cierre programado, se cierra solo cuando se agota la
    secuencia de órdenes (ver avanzar_orden en operacion.py).

    orden_activa_fk es la fuente AUTORITATIVA de "qué orden está
    corriendo ahora" para las líneas que adoptan este flujo -- sólo
    cambia vía avanzar_orden (acción de supervisor), nunca automático.
    Reemplaza, para esas líneas, al heurístico "orden EN_PROGRESO más
    reciente" que scans.py usa de fallback (Fase P) -- ver
    resolver_orden_activa en clasificacion.py. Las líneas que no crean
    un Plan siguen exactamente con el comportamiento de siempre: Plan es
    opcional, no se le impone a ningún tenant que no lo pida."""
    __tablename__ = "planes_produccion"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    linea_id: uuid.UUID = Field(foreign_key="dim_lineas.id")
    # Unificación UX Planes/Órdenes/SKUs (pedido Green Mills): antes un
    # plan sólo se identificaba por (línea, fecha) -- sin nombre, y la
    # única UI (Supervisor) asumía que había A LO SUMO uno abierto por
    # línea/día. Ahora puede haber varios el mismo día (turnos, lotes
    # urgentes) y hace falta poder identificarlos. Nullable en el modelo
    # -- los planes ya creados antes de este cambio no tienen backfill
    # razonable -- pero la API exige nombre no vacío en el alta desde acá
    # en adelante (ver PlanProduccionCreate).
    nombre: Optional[str] = Field(default=None)
    fecha_inicio: date
    estado: EstadoPlan = Field(default=EstadoPlan.EN_PROGRESO)
    # NOTA (C1/C2, mismo criterio que el resto del esquema): apunta a
    # OrdenProduccion.id (UUID), no a id_orden (PK legacy) -- las FKs
    # nuevas hacia Orden usan la identidad interna, ver nota en esa clase.
    orden_activa_fk: Optional[uuid.UUID] = Field(default=None, foreign_key="ordenes_produccion.id")
    activo: bool = Field(default=True)
    # Fase AV (auditoría de frontend, FE-P0-04): sólo se completa cuando
    # desactivar_plan cancela un plan EN_PROGRESO (obligatorio ahí,
    # validado en el endpoint) -- archivar un BORRADOR/PROGRAMADO que
    # nunca arrancó no lo exige. Null para todo plan que se cerró solo
    # (CERRADO vía avanzar_orden) o que nunca se desactivó.
    motivo_cancelacion: Optional[str] = Field(default=None)


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

    # Fase AA: si pertenece a un Plan, plan_id la vincula y secuencia
    # define su lugar en la fila (1, 2, 3...) -- avanzar_orden usa
    # secuencia para decidir cuál sigue. None = orden suelta, fuera de
    # cualquier plan (comportamiento histórico, sin cambios).
    plan_id: Optional[uuid.UUID] = Field(default=None, foreign_key="planes_produccion.id")
    secuencia: int = Field(default=0)

    plan_fecha: Optional[str] = Field(default=None, description="Ej: YYYY-MM-DD")
    estado: EstadoOrden = Field(default=EstadoOrden.ABIERTA)
    origen: str = Field(default="UI")
    activo: bool = Field(default=True)
    # Fase AX (auditoría de frontend, FE-P0-06): avanzar_orden (operacion.py)
    # exige este motivo cuando cierra la orden con menos unidades reales
    # (sumadas de LiteEventoProduccion, nunca cantidad_producida -- ver
    # comentario de esa columna) que cantidad_esperada. Null si se cerró
    # completa/sobreproducida.
    motivo_incompleta: Optional[str] = Field(default=None)

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
    # Fase CB (auditoría de robustez, batch 3): trazabilidad de quién
    # emitió la credencial y si sigue viva. `creado_por_id` opcional
    # porque las keys emitidas antes de esta fase no tienen ese dato
    # (no se puede reconstruir retroactivamente). `ultimo_uso_at` lo
    # actualiza autenticar_dispositivo (auth_m2m.py) con una ventana de
    # 5 minutos -- ver el comentario ahí sobre por qué NO se escribe en
    # cada auth (una línea activa autentica un scan por pieza).
    creado_por_id: Optional[uuid.UUID] = Field(default=None, foreign_key="usuarios_saas.id")
    ultimo_uso_at: Optional[datetime] = Field(default=None)

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
    # Fase AF (pedido de Green Mills): qué orden -- y por lo tanto qué SKU
    # -- estaba activa cuando se detectó la parada, para poder analizar
    # paradas POR SKU (ver /analytics/paradas-por-sku/). NULL para paradas
    # de antes de este campo (no se backfillea con una suposición; quedan
    # agrupadas como "Sin SKU asociado") y para paradas PLANIFICADAS (no
    # están atadas a un evento de scans.py). Apunta a OrdenProduccion.id
    # (UUID), no a id_orden -- mismo criterio C1/C2 que el resto de las
    # FKs nuevas desde Fase AA (ver nota en OrdenProduccion).
    orden_fk: Optional[uuid.UUID] = Field(default=None, foreign_key="ordenes_produccion.id")

    # Fase CC (FE-P0-08): workflow de falso positivo -- ver
    # EstadoExclusionOee. Todos nullable/con default NINGUNA porque las
    # paradas existentes antes de esta fase nunca pasaron por el flujo.
    exclusion_oee: EstadoExclusionOee = Field(default=EstadoExclusionOee.NINGUNA)
    exclusion_motivo: Optional[str] = Field(default=None, description="Por qué se propone como falso positivo")
    exclusion_propuesta_por_id: Optional[uuid.UUID] = Field(default=None, foreign_key="usuarios_saas.id")
    exclusion_propuesta_at: Optional[datetime] = Field(default=None)
    exclusion_resuelta_por_id: Optional[uuid.UUID] = Field(default=None, foreign_key="usuarios_saas.id")
    exclusion_resuelta_at: Optional[datetime] = Field(default=None)
    exclusion_resolucion_nota: Optional[str] = Field(default=None)

    # Fase CK (diferenciadores P2, batch 3): quién clasificó la parada --
    # hasta acá el historial mostraba el motivo pero no el responsable de
    # la decisión. NULL para toda parada clasificada antes de esta fase
    # (no se backfillea con una suposición). Mismo criterio de FK que
    # exclusion_propuesta_por_id/exclusion_resuelta_por_id.
    clasificado_por_id: Optional[uuid.UUID] = Field(default=None, foreign_key="usuarios_saas.id")

    # Fase DU (P0-05 revisado): auditoría de anulación -- mismo criterio
    # que el resto de los "quién/cuándo/por qué" de este modelo.
    motivo_anulacion: Optional[str] = Field(default=None)
    anulada_por_id: Optional[uuid.UUID] = Field(default=None, foreign_key="usuarios_saas.id")
    anulada_at: Optional[datetime] = Field(default=None)


class RegistroAuditoria(TenantBase, table=True):
    """Fase DU (auditoría de backend, P1-02 parcial): tabla de auditoría
    GENÉRICA y reusable -- no existía ningún mecanismo de este tipo en
    todo el backend antes de esta fase (recomputo.py documentaba
    explícitamente la ausencia, ver comentario histórico de Fase S).
    Primer consumidor: recomputar_eventos (operacion.py/recomputo.py).
    Pensada para reusarse después en cualquier acción que necesite un
    rastro de quién/cuándo/qué/por qué (ej. futuro historial de
    reclasificaciones) sin duplicar esta infraestructura cada vez.

    El cierre/reapertura formal de período (la otra mitad de P1-02)
    queda explícitamente FUERA todavía -- necesita una decisión de
    producto (qué cierra un período, quién puede reabrirlo y bajo qué
    condición) que no está definida; no se inventa acá."""
    __tablename__ = "registros_auditoria"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    entidad: str = Field(index=True, description="Tipo de acción auditada, ej. 'recomputo_eventos'")
    entidad_id: Optional[str] = Field(default=None, index=True, description="ID del recurso afectado, ej. estacion_id")
    accion: str = Field(description="Verbo corto de la acción, ej. 'ejecutar'")
    usuario_id: uuid.UUID = Field(foreign_key="usuarios_saas.id")
    detalle: Optional[str] = Field(default=None, description="Resumen legible del contexto/resultado")
    creado_at: datetime = Field(default_factory=datetime.utcnow, index=True)


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
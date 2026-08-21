from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel, field_validator
from typing import List, Optional
from sqlalchemy import func
import uuid

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant_humano, TenantContext, get_usuario_actual, verificar_token_auth0
from app.core.rbac import requerir_gerencia_o_superadmin, requerir_superadmin
from app.core.auth0_management import crear_ticket_cambio_password
from app.core.onboarding import invitar_usuario_en_auth0, provisionar_tenant_y_usuario
from app.models.domain import (
    UsuarioSaaS, RolUsuario, Tenant, Planta, UsuarioPlanta, EstadoTenant, ModuloPermiso,
    ModuloDisponible,
)

# Roles a los que no se les asigna alcance por planta -- ven todo el tenant
# sin necesitar filas explícitas (mismo criterio ya usado por UsuarioPlanta).
ROLES_ALCANCE_COMPLETO = (RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.PRODUCCION)

router = APIRouter(prefix="/accesos", tags=["Administración SaaS y RBAC"])

# ==========================================
# MOLDES (SCHEMAS)
# ==========================================
class UsuarioCreate(BaseModel):
    email: str
    nombre: str
    apellido: str
    rol: RolUsuario
    auth0_id: Optional[str] = None

class UsuarioUpdate(BaseModel):
    rol: Optional[RolUsuario] = None
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    activo: Optional[bool] = None

class NuevoUsuarioSaaS(BaseModel):
    tenant_id: str
    email: str
    rol: RolUsuario
    nombre: str
    apellido: str

class UsuarioGlobalUpdate(BaseModel):
    tenant_id: Optional[str] = None
    rol: Optional[RolUsuario] = None
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    activo: Optional[bool] = None

class TenantCreate(BaseModel):
    id: str
    nombre: str

class TenantEstadoUpdate(BaseModel):
    estado: EstadoTenant

class TenantUpdate(BaseModel):
    nombre: Optional[str] = None
    color_primario: Optional[str] = None
    logo_url: Optional[str] = None
    # Fase AC: tolerancia_lento_pct/alerta_pct retirados -- Empresa deja
    # de ser un nivel de la cascada de umbrales (ver Tenant en domain.py
    # y clasificacion.resolver_umbrales_evento). El piso ahora es Línea.
    # Fase N: objetivo de OEE configurable (antes hardcodeado en el front:
    # 75 en la tendencia, 85 en el Command Center).
    oee_objetivo_pct: Optional[float] = None
    # Fase AF (reorganización de "carga de SKUs por el front", pedido de
    # Green Mills): origen_maestros ya existía en el modelo y YA estaba
    # enforced en el backend (verificar_permiso_carga_y_linea, importaciones.py
    # -- rechaza con 409 la carga manual/archivo si es "ERP"), pero nunca
    # tuvo forma de editarse por API -- un gap real, no un campo nuevo.
    # Fase EZ.2: origen_maestros ahora gobierna sólo SKUs.
    origen_maestros: Optional[str] = None
    # Fase EZ.2: split de origen_maestros -- gobierna Planes y Órdenes,
    # independiente del de SKUs (ver Tenant.origen_maestros_planes).
    origen_maestros_planes: Optional[str] = None

class TenantLogoUpdate(BaseModel):
    logo_url: str

class TenantModulosUpdate(BaseModel):
    modulos_contratados: List[str]

    @field_validator("modulos_contratados")
    @classmethod
    def _normalizar_modulos(cls, v: List[str]) -> List[str]:
        # Fase EB (PRD Billing MVP, confirmado con el usuario): la
        # validación de "¿es un módulo REAL?" se movió al endpoint (ver
        # actualizar_modulos_tenant) -- ahora consulta ModuloDisponible
        # en vez de MODULOS_VALIDOS (constante hardcodeada, retirada).
        # Acá sólo queda la normalización estructural, que un
        # field_validator sí puede hacer sin sesión de DB.
        return [m.strip().lower() for m in v]

class TicketCambioPassword(BaseModel):
    ticket_url: str

class UsuarioPlantaCreate(BaseModel):
    usuario_id: uuid.UUID
    planta_id: uuid.UUID

class UsuarioPlantaResponse(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID
    planta_id: uuid.UUID
    activo: bool

class ModuloPermisoCreate(BaseModel):
    usuario_id: uuid.UUID
    modulo: str
    planta_id: uuid.UUID
    rol: RolUsuario

class ModuloPermisoResponse(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID
    modulo: str
    planta_id: uuid.UUID
    rol: RolUsuario
    activo: bool

class PermisoMe(BaseModel):
    modulo: str
    planta_id: Optional[uuid.UUID] = None
    rol: RolUsuario

class PerfilMeResponse(BaseModel):
    id: uuid.UUID
    auth0_id: str
    tenant_id: str
    email: Optional[str] = None
    rol: RolUsuario
    activo: bool
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    permisos: List[PermisoMe]


# ==========================================
# RUTAS DE PERFIL (FRONTEND BOOTSTRAP)
# ==========================================
def resolver_o_provisionar_usuario_actual(
    payload: dict = Depends(verificar_token_auth0),
    db: Session = Depends(get_session),
) -> UsuarioSaaS:
    """Fase EU: variante de get_usuario_actual() SÓLO para el bootstrap de
    perfil (/usuarios/me) -- get_usuario_actual() en sí NO se toca, lo
    siguen usando decenas de otros endpoints con su 403 duro intacto.
    Auto-provisionar como side-effect de cualquier request random sería
    un riesgo real (carreras, tenants fantasma); acá es seguro porque es
    el único endpoint que el frontend llama, una vez, justo después del
    login -- exactamente donde tiene sentido resolver el "limbo" de un
    usuario de Auth0 sin Tenant/UsuarioSaaS asociado todavía."""
    auth0_sub = payload.get("sub")
    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    if not usuario_db:
        usuario_db = provisionar_tenant_y_usuario(auth0_sub, db)
    if not usuario_db.activo:
        raise HTTPException(status_code=403, detail="Usuario inactivo.")
    return usuario_db


@router.get("/usuarios/me", response_model=PerfilMeResponse, tags=["Perfil"])
def obtener_perfil_actual(
    usuario_actual: UsuarioSaaS = Depends(resolver_o_provisionar_usuario_actual),
    db: Session = Depends(get_session),
):
    """Devuelve los datos del usuario logueado más su matriz de permisos por
    módulo y planta, para que el Frontend arme el menú y el gating de rutas.

    SUPERADMIN/GERENCIA/PRODUCCION no tienen filas en ModuloPermiso (ven todo
    el tenant, igual que ya pasa con UsuarioPlanta): se les sintetiza un
    permiso de alcance completo (planta_id=None) por cada módulo contratado
    por su tenant. SUPERVISOR/OPERARIO sólo ven los módulos/plantas que
    tengan explícitamente asignados.
    """
    tenant = db.get(Tenant, usuario_actual.tenant_id)
    modulos_contratados = [
        m.strip().lower() for m in (tenant.modulos_contratados or "").split(",") if m.strip()
    ] if tenant else []

    if usuario_actual.rol in ROLES_ALCANCE_COMPLETO:
        permisos = [
            PermisoMe(modulo=modulo, planta_id=None, rol=usuario_actual.rol)
            for modulo in modulos_contratados
        ]
    else:
        filas = db.exec(
            select(ModuloPermiso).where(
                ModuloPermiso.usuario_id == usuario_actual.id,
                ModuloPermiso.activo == True,  # noqa: E712
            )
        ).all()
        permisos = [PermisoMe(modulo=f.modulo, planta_id=f.planta_id, rol=f.rol) for f in filas]

    return PerfilMeResponse(
        id=usuario_actual.id,
        auth0_id=usuario_actual.auth0_id,
        tenant_id=usuario_actual.tenant_id,
        email=usuario_actual.email,
        rol=usuario_actual.rol,
        activo=usuario_actual.activo,
        nombre=usuario_actual.nombre,
        apellido=usuario_actual.apellido,
        permisos=permisos,
    )


# ==========================================
# GESTIÓN DE LA PROPIA EMPRESA (MI EMPRESA)
# ==========================================
@router.get("/mi-empresa/tenant", tags=["Gestión de Accesos (Empresa)"])
def obtener_mi_tenant(
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Devuelve la configuración de la empresa actual (Branding, umbrales)."""
    tenant_db = db.exec(select(Tenant).where(Tenant.id == context.tenant_id)).first()
    if not tenant_db:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    return tenant_db

@router.patch("/mi-empresa/tenant", tags=["Gestión de Accesos (Empresa)"])
def actualizar_mi_tenant(
    datos: TenantUpdate, 
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    """Permite a la Gerencia actualizar el branding y reglas de su propia empresa."""
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA]:
        raise HTTPException(status_code=403, detail="Solo Gerencia puede modificar la configuración de la empresa.")

    tenant_db = db.exec(select(Tenant).where(Tenant.id == context.tenant_id)).first()
    
    update_data = datos.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tenant_db, key, value)

    db.add(tenant_db)
    db.commit()
    db.refresh(tenant_db)
    return {"mensaje": "Configuración de empresa actualizada", "tenant": tenant_db}


@router.post("/mi-empresa/tenant/logo", response_model=TenantLogoUpdate, tags=["Gestión de Accesos (Empresa)"])
def actualizar_logo_tenant(
    payload: TenantLogoUpdate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    """Setea el branding del tenant a partir de una URL ya alojada (sin
    upload de archivo: no hay object storage conectado todavía). El front
    sube el archivo adonde corresponda y manda acá la URL resultante."""
    tenant_db = db.get(Tenant, context.tenant_id)
    if not tenant_db:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    tenant_db.logo_url = payload.logo_url
    db.add(tenant_db)
    db.commit()
    db.refresh(tenant_db)
    return TenantLogoUpdate(logo_url=tenant_db.logo_url)


# ==========================================
# ENDPOINTS DE USUARIOS (MI EMPRESA)
# ==========================================
@router.get("/mi-empresa/usuarios", response_model=List[dict])
def listar_usuarios_tenant(
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.SUPERVISOR]:
        raise HTTPException(status_code=403, detail="No tienes permisos para listar usuarios.")

    query = select(UsuarioSaaS).where(UsuarioSaaS.tenant_id == context.tenant_id)

    if usuario_actual.rol == RolUsuario.SUPERVISOR:
        # RBAC geolocalizado (Fase D.3): un Supervisor sólo ve usuarios que
        # comparten al menos una planta con él, más Gerencia/SuperAdmin
        # (que no están acotados por planta). Antes veía el tenant entero.
        mis_plantas = db.exec(
            select(UsuarioPlanta.planta_id).where(
                UsuarioPlanta.usuario_id == usuario_actual.id,
                UsuarioPlanta.activo == True,  # noqa: E712
            )
        ).all()
        usuarios_compartidos_ids = set(db.exec(
            select(UsuarioPlanta.usuario_id).where(
                UsuarioPlanta.planta_id.in_(mis_plantas),
                UsuarioPlanta.activo == True,  # noqa: E712
            )
        ).all()) if mis_plantas else set()

        query = query.where(
            UsuarioSaaS.id.in_(usuarios_compartidos_ids)
            | UsuarioSaaS.rol.in_([RolUsuario.GERENCIA, RolUsuario.SUPERADMIN])
        )

    usuarios = db.exec(query).all()
    return [
        {
            "id": str(u.id), "auth0_id": u.auth0_id, "email": u.email,
            "nombre": u.nombre, "apellido": u.apellido, "rol": u.rol.value, "activo": u.activo
        } for u in usuarios
    ]

@router.get("/mi-empresa/usuarios/{auth0_id_target}", response_model=dict)
def obtener_usuario_tenant(
    auth0_id_target: str,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
):
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.SUPERVISOR]:
        raise HTTPException(status_code=403, detail="No tienes permisos para ver usuarios.")

    u = db.exec(select(UsuarioSaaS).where(
        UsuarioSaaS.auth0_id == auth0_id_target, UsuarioSaaS.tenant_id == context.tenant_id
    )).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    if usuario_actual.rol == RolUsuario.SUPERVISOR and u.rol not in (RolUsuario.GERENCIA, RolUsuario.SUPERADMIN):
        # Mismo alcance geolocalizado que el listado (Fase D.3): sólo si comparten planta.
        comparten_planta = db.exec(
            select(UsuarioPlanta).where(
                UsuarioPlanta.usuario_id == usuario_actual.id,
                UsuarioPlanta.activo == True,  # noqa: E712
                UsuarioPlanta.planta_id.in_(
                    select(UsuarioPlanta.planta_id).where(
                        UsuarioPlanta.usuario_id == u.id, UsuarioPlanta.activo == True  # noqa: E712
                    )
                ),
            )
        ).first()
        if not comparten_planta:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    return {
        "id": str(u.id), "auth0_id": u.auth0_id, "email": u.email,
        "nombre": u.nombre, "apellido": u.apellido, "rol": u.rol.value, "activo": u.activo
    }


@router.post("/mi-empresa/usuarios", status_code=status.HTTP_201_CREATED)
def crear_usuario_tenant(
    payload: UsuarioCreate,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    # P1-A (PRD Go-Live Green Mills, sección 5): Producción SÍ puede crear
    # usuarios (Encargado/Supervisor/Producción) -- antes este endpoint
    # la bloqueaba por completo, aunque el sidebar y las rutas del
    # frontend ya la dejaban entrar a la pantalla (gap real, no sólo de
    # UI). Nunca puede escalar hacia arriba: ni crear Gerencia/SuperAdmin.
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.PRODUCCION]:
        raise HTTPException(status_code=403, detail="Solo Gerencia, Producción o SuperAdmin pueden crear usuarios.")

    if usuario_actual.rol == RolUsuario.GERENCIA and payload.rol == RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Infracción RBAC: Un Gerente no puede crear un SuperAdmin.")

    if usuario_actual.rol == RolUsuario.PRODUCCION and payload.rol in (RolUsuario.GERENCIA, RolUsuario.SUPERADMIN):
        raise HTTPException(status_code=403, detail="Producción no puede crear un usuario con rol Gerencia o SuperAdmin.")

    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == payload.email)).first():
        raise HTTPException(status_code=400, detail="El correo electrónico ya está registrado.")

    # Fase EV: si el caller ya pasó un auth0_id explícito (poco común, ver
    # UsuarioCreate.auth0_id), se respeta tal cual -- no se intenta crear
    # en Auth0 de nuevo. Si no, se intenta el alta real (best-effort, cae
    # al placeholder mock si falla -- ver invitar_usuario_en_auth0).
    if payload.auth0_id:
        auth0_id, auth0_creado, ticket_url = payload.auth0_id, False, None
    else:
        auth0_id, auth0_creado, ticket_url = invitar_usuario_en_auth0(payload.email, payload.nombre, payload.apellido)

    nuevo_usuario = UsuarioSaaS(
        auth0_id=auth0_id, tenant_id=context.tenant_id, email=payload.email,
        nombre=payload.nombre, apellido=payload.apellido, rol=payload.rol, activo=True
    )
    db.add(nuevo_usuario)
    db.commit()
    return {
        "mensaje": "Usuario creado con éxito", "id": str(nuevo_usuario.id),
        "auth0_creado": auth0_creado, "ticket_url": ticket_url,
    }

@router.patch("/mi-empresa/usuarios/{auth0_id_target}")
def actualizar_usuario(
    auth0_id_target: str,
    payload: UsuarioUpdate,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant_humano)
):
    usuario_target = db.exec(select(UsuarioSaaS).where(
        UsuarioSaaS.auth0_id == auth0_id_target, UsuarioSaaS.tenant_id == context.tenant_id
    )).first()

    if not usuario_target: raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    # P1-A: este endpoint no tenía NINGÚN piso de rol -- cualquier
    # usuario autenticado del tenant (hasta un Operario) podía cambiarle
    # el rol o desactivar (revocar acceso) a cualquier otro usuario.
    # Hallazgo real de pasada al implementar P1-A, no sólo el pedido
    # original del PRD (Producción no escala) -- se cierra acá porque el
    # piso de rol es un prerequisito para poder distinguir a Producción
    # del resto.
    if usuario_actual.rol not in (RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.PRODUCCION):
        raise HTTPException(status_code=403, detail="Solo Gerencia, Producción o SuperAdmin pueden modificar usuarios.")

    if usuario_actual.auth0_id == auth0_id_target and payload.rol and payload.rol != usuario_target.rol:
        raise HTTPException(status_code=403, detail="No puedes auto-modificar tu rol.")
    if usuario_actual.rol == RolUsuario.GERENCIA and usuario_target.rol == RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="No tienes autoridad sobre un SuperAdmin.")
    # P1-A: Producción no tiene autoridad sobre un usuario Gerencia/
    # SuperAdmin YA EXISTENTE (ninguna modificación, incluida revocar
    # acceso vía activo=False) -- ni puede ASCENDER a alguien a esos
    # roles (payload.rol), aunque el target hoy sea Encargado/Supervisor.
    if usuario_actual.rol == RolUsuario.PRODUCCION:
        if usuario_target.rol in (RolUsuario.GERENCIA, RolUsuario.SUPERADMIN):
            raise HTTPException(status_code=403, detail="Producción no tiene autoridad sobre un usuario de Gerencia o SuperAdmin.")
        if payload.rol in (RolUsuario.GERENCIA, RolUsuario.SUPERADMIN):
            raise HTTPException(status_code=403, detail="Producción no puede asignar el rol Gerencia o SuperAdmin.")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items(): setattr(usuario_target, key, value)

    db.add(usuario_target)
    db.commit()
    return {"mensaje": "Usuario actualizado con éxito"}


# ==========================================
# ENDPOINTS SUPERADMIN (SaaS Core Global)
# ==========================================
@router.get("/superadmin/tenants")
def listar_todos_los_tenants(db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    # `modulos_contratados` se agrega acá (Fase O, seguimiento de auditoría
    # #2): el panel SaaS necesitaba el dato para mostrar/editar los
    # switches de "Módulos contratados" sin un fetch extra por tenant.
    # Válido en GROUP BY sin agregarlo ahí: depende funcionalmente de
    # Tenant.id (su PK), Postgres lo permite igual que con nombre/logo_url.
    stmt = select(
        Tenant.id, Tenant.nombre, Tenant.color_primario, Tenant.logo_url,
        Tenant.modulos_contratados, func.count(UsuarioSaaS.id).label("total_usuarios")
    ).outerjoin(UsuarioSaaS, Tenant.id == UsuarioSaaS.tenant_id).group_by(Tenant.id)
    return [
        {
            "id": r.id, "nombre": r.nombre, "color_primario": r.color_primario,
            "logo_url": r.logo_url, "modulos_contratados": r.modulos_contratados,
            "total_usuarios": r.total_usuarios,
        }
        for r in db.exec(stmt).all()
    ]

@router.post("/superadmin/tenants", tags=["SuperAdmin (Global)"])
def crear_tenant_global(datos: TenantCreate, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    if db.exec(select(Tenant).where(Tenant.id == datos.id)).first():
        raise HTTPException(status_code=400, detail="El ID del tenant ya existe.")
        
    nuevo_tenant = Tenant(id=datos.id, nombre=datos.nombre)
    db.add(nuevo_tenant)
    db.commit()
    db.refresh(nuevo_tenant)
    return {"mensaje": "Empresa cliente creada exitosamente", "tenant": nuevo_tenant}

@router.get("/superadmin/tenants/{tenant_id}", tags=["SuperAdmin (Global)"])
def obtener_tenant_global(tenant_id: str, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    tenant = db.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    return tenant

@router.patch("/superadmin/tenants/{tenant_id}", tags=["SuperAdmin (Global)"])
def actualizar_tenant_global(tenant_id: str, payload: TenantUpdate, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    """Actualiza branding/tolerancias de cualquier tenant (no el estado de
    suspensión, que tiene su propio endpoint dedicado más abajo)."""
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    tenant = db.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    datos = payload.model_dump(exclude_unset=True)
    for key, value in datos.items():
        setattr(tenant, key, value)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant

@router.patch("/superadmin/tenants/{tenant_id}/modulos", tags=["SuperAdmin (Global)"])
def actualizar_modulos_tenant(
    tenant_id: str,
    payload: TenantModulosUpdate,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
):
    """Módulos contratados por el tenant (Fase M). Sólo SuperAdmin -- es una
    decisión comercial/de billing, no algo que el propio tenant se
    autoasigne desde /mi-empresa/tenant (por eso vive en un endpoint
    separado del PATCH genérico de tenant, no en TenantUpdate).

    Fase EB (PRD Billing MVP, confirmado con el usuario): "¿es un módulo
    real?" ya no se valida contra MODULOS_VALIDOS (constante hardcodeada,
    retirada) -- consulta ModuloDisponible, la fuente de verdad real."""
    if usuario_actual.rol != RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    tenant = db.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")

    codigos_pedidos = set(payload.modulos_contratados)
    if codigos_pedidos:
        codigos_reales = set(db.exec(select(ModuloDisponible.codigo)).all())
        invalidos = codigos_pedidos - codigos_reales
        if invalidos:
            raise HTTPException(
                status_code=422,
                detail=f"Módulos inválidos: {sorted(invalidos)}. Válidos: {sorted(codigos_reales)}",
            )

    tenant.modulos_contratados = ",".join(sorted(codigos_pedidos))
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return {"mensaje": f"Módulos de '{tenant_id}' actualizados.", "tenant": tenant}


@router.patch("/superadmin/tenants/{tenant_id}/estado", tags=["SuperAdmin (Global)"])
def cambiar_estado_empresa(
    tenant_id: str,
    payload: TenantEstadoUpdate,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
):
    """Cambia el estado de suspensión del tenant (ACTIVO/UI_SUSPENDIDA/SUSPENSION_TOTAL).

    Reemplaza al kill-switch anterior, que desactivaba usuario por usuario
    y nunca tocaba Tenant.estado. Sólo SuperAdmin puede cambiarlo.
    - UI_SUSPENDIDA: los endpoints humanos (dashboards, configuración) devuelven
      403, pero /me y el Edge/M2M siguen funcionando.
    - SUSPENSION_TOTAL: además corta Edge/M2M.
    """
    if usuario_actual.rol != RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")

    tenant_db = db.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
    if not tenant_db:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")

    tenant_db.estado = payload.estado
    db.add(tenant_db)
    db.commit()
    db.refresh(tenant_db)
    return {"mensaje": f"Estado de '{tenant_id}' actualizado a '{payload.estado.value}'.", "tenant": tenant_db}

@router.get("/superadmin/usuarios")
def listar_todos_los_usuarios_globales(db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    return db.exec(select(UsuarioSaaS)).all()

@router.get("/superadmin/usuarios/{auth0_id}", tags=["SuperAdmin (Global)"])
def obtener_usuario_global(auth0_id: str, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    usuario = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return usuario

@router.post("/superadmin/usuarios", status_code=status.HTTP_201_CREATED)
def crear_usuario_b2b(nuevo_usuario: NuevoUsuarioSaaS, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == nuevo_usuario.email)).first():
        raise HTTPException(status_code=400, detail="Email ya registrado.")
    
    # Fase EV: alta real en Auth0, best-effort (ver invitar_usuario_en_auth0
    # -- cae al placeholder mock de siempre si falla).
    auth0_id, auth0_creado, ticket_url = invitar_usuario_en_auth0(
        nuevo_usuario.email, nuevo_usuario.nombre, nuevo_usuario.apellido
    )
    db_usuario = UsuarioSaaS(
        auth0_id=auth0_id, tenant_id=nuevo_usuario.tenant_id, email=nuevo_usuario.email,
        rol=nuevo_usuario.rol, nombre=nuevo_usuario.nombre, apellido=nuevo_usuario.apellido, activo=True
    )
    db.add(db_usuario)
    db.commit()
    # Bug real preexistente (no de Fase EV): sin refresh acá, el objeto
    # serializaba vacío en la respuesta -- expire_on_commit por default
    # deja sus atributos "expirados", y para cuando FastAPI lo serializa
    # la sesión de esta request ya se cerró (no puede hacer lazy-load).
    db.refresh(db_usuario)
    return {
        "mensaje": "Usuario B2B creado exitosamente.", "usuario": db_usuario,
        "auth0_creado": auth0_creado, "ticket_url": ticket_url,
    }

@router.patch("/superadmin/usuarios/{auth0_id}", tags=["SuperAdmin (Global)"])
def actualizar_usuario_global(auth0_id: str, payload: UsuarioGlobalUpdate, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    usuario_target = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario_target: raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    if usuario_actual.auth0_id == auth0_id and payload.rol and payload.rol != RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=400, detail="No puedes auto-degradarte de SuperAdmin.")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items(): setattr(usuario_target, key, value)
        
    db.add(usuario_target)
    db.commit()
    return {"mensaje": "Usuario actualizado globalmente con éxito."}

@router.delete("/superadmin/usuarios/{auth0_id}", tags=["SuperAdmin (Global)"])
def eliminar_usuario_global(auth0_id: str, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    """Baja lógica (activo=False). Antes era DELETE físico -- violaba "cero
    hard-deletes" y rompía la trazabilidad de auditoría/creaciones/eventos
    asociados a ese usuario; corregido."""
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    usuario_target = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario_target: raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if usuario_actual.auth0_id == auth0_id: raise HTTPException(status_code=400, detail="Operación suicida bloqueada.")

    usuario_target.activo = False
    db.add(usuario_target)
    db.commit()
    return {"mensaje": "Usuario desactivado."}


@router.post("/superadmin/usuarios/{auth0_id}/reset-password", response_model=TicketCambioPassword, tags=["SuperAdmin (Global)"])
def resetear_password_usuario(
    auth0_id: str,
    db: Session = Depends(get_session),
    _: UsuarioSaaS = Depends(requerir_superadmin),
):
    """Genera un link de cambio de password de un solo uso vía Auth0
    Management API (ticket, no resetea directamente -- requiere sólo el
    scope create:user_tickets, no update:users)."""
    usuario_target = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario_target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    ticket_url = crear_ticket_cambio_password(auth0_id)
    return TicketCambioPassword(ticket_url=ticket_url)


# ==========================================
# RBAC GEOLOCALIZADO: ASIGNACIÓN USUARIO-PLANTA (Fase D.1)
# Aplica sólo a roles SUPERVISOR y OPERARIO; Gerencia/SuperAdmin ven
# todo el tenant sin necesitar asignación.
# ==========================================
@router.post("/mi-empresa/usuario-planta", response_model=UsuarioPlantaResponse, status_code=status.HTTP_201_CREATED, tags=["RBAC Geolocalizado"])
def asignar_usuario_a_planta(
    payload: UsuarioPlantaCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    usuario_target = db.exec(
        select(UsuarioSaaS).where(UsuarioSaaS.id == payload.usuario_id, UsuarioSaaS.tenant_id == context.tenant_id)
    ).first()
    if not usuario_target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en este tenant.")

    if usuario_target.rol not in [RolUsuario.SUPERVISOR, RolUsuario.OPERARIO]:
        raise HTTPException(
            status_code=400,
            detail="El alcance por planta sólo aplica a usuarios SUPERVISOR u OPERARIO.",
        )

    planta_target = db.exec(
        select(Planta).where(Planta.id == payload.planta_id, Planta.tenant_id == context.tenant_id)
    ).first()
    if not planta_target:
        raise HTTPException(status_code=404, detail="Planta no encontrada en este tenant.")

    existente = db.exec(
        select(UsuarioPlanta).where(
            UsuarioPlanta.usuario_id == payload.usuario_id,
            UsuarioPlanta.planta_id == payload.planta_id,
            UsuarioPlanta.activo == True,  # noqa: E712
        )
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ese usuario ya está asignado a esa planta.")

    nueva_asignacion = UsuarioPlanta(
        tenant_id=context.tenant_id,
        usuario_id=payload.usuario_id,
        planta_id=payload.planta_id,
    )
    db.add(nueva_asignacion)
    db.commit()
    db.refresh(nueva_asignacion)
    return nueva_asignacion


@router.get("/mi-empresa/usuario-planta", response_model=List[UsuarioPlantaResponse], tags=["RBAC Geolocalizado"])
def listar_asignaciones_usuario_planta(
    usuario_id: Optional[uuid.UUID] = None,
    planta_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    query = select(UsuarioPlanta).where(UsuarioPlanta.tenant_id == context.tenant_id, UsuarioPlanta.activo == True)  # noqa: E712
    if usuario_id:
        query = query.where(UsuarioPlanta.usuario_id == usuario_id)
    if planta_id:
        query = query.where(UsuarioPlanta.planta_id == planta_id)
    return db.exec(query).all()


@router.delete("/mi-empresa/usuario-planta/{asignacion_id}", tags=["RBAC Geolocalizado"])
def quitar_asignacion_usuario_planta(
    asignacion_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    asignacion = db.exec(
        select(UsuarioPlanta).where(UsuarioPlanta.id == asignacion_id, UsuarioPlanta.tenant_id == context.tenant_id)
    ).first()
    if not asignacion:
        raise HTTPException(status_code=404, detail="Asignación no encontrada.")

    asignacion.activo = False
    db.add(asignacion)
    db.commit()
    return {"mensaje": "Asignación desactivada."}


# ==========================================
# PERMISOS POR MÓDULO Y PLANTA (Fase F, InspectIA OS)
# Aplica sólo a roles SUPERVISOR y OPERARIO; Gerencia/SuperAdmin/Producción
# ven todos los módulos contratados sin necesitar filas acá (ver /me).
# ==========================================
@router.post("/mi-empresa/modulo-permiso", response_model=ModuloPermisoResponse, status_code=status.HTTP_201_CREATED, tags=["Permisos por Módulo"])
def asignar_permiso_modulo(
    payload: ModuloPermisoCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    usuario_target = db.exec(
        select(UsuarioSaaS).where(UsuarioSaaS.id == payload.usuario_id, UsuarioSaaS.tenant_id == context.tenant_id)
    ).first()
    if not usuario_target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en este tenant.")

    if usuario_target.rol in ROLES_ALCANCE_COMPLETO:
        raise HTTPException(
            status_code=400,
            detail="Gerencia/SuperAdmin/Producción ya ven todos los módulos contratados; no necesitan permiso explícito.",
        )

    planta_target = db.exec(
        select(Planta).where(Planta.id == payload.planta_id, Planta.tenant_id == context.tenant_id)
    ).first()
    if not planta_target:
        raise HTTPException(status_code=404, detail="Planta no encontrada en este tenant.")

    existente = db.exec(
        select(ModuloPermiso).where(
            ModuloPermiso.usuario_id == payload.usuario_id,
            ModuloPermiso.modulo == payload.modulo,
            ModuloPermiso.planta_id == payload.planta_id,
            ModuloPermiso.activo == True,  # noqa: E712
        )
    ).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ese usuario ya tiene ese módulo asignado en esa planta.")

    nuevo_permiso = ModuloPermiso(
        tenant_id=context.tenant_id,
        usuario_id=payload.usuario_id,
        modulo=payload.modulo,
        planta_id=payload.planta_id,
        rol=payload.rol,
    )
    db.add(nuevo_permiso)
    db.commit()
    db.refresh(nuevo_permiso)
    return nuevo_permiso


@router.get("/mi-empresa/modulo-permiso", response_model=List[ModuloPermisoResponse], tags=["Permisos por Módulo"])
def listar_permisos_modulo(
    usuario_id: Optional[uuid.UUID] = None,
    planta_id: Optional[uuid.UUID] = None,
    modulo: Optional[str] = None,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    query = select(ModuloPermiso).where(ModuloPermiso.tenant_id == context.tenant_id, ModuloPermiso.activo == True)  # noqa: E712
    if usuario_id:
        query = query.where(ModuloPermiso.usuario_id == usuario_id)
    if planta_id:
        query = query.where(ModuloPermiso.planta_id == planta_id)
    if modulo:
        query = query.where(ModuloPermiso.modulo == modulo)
    return db.exec(query).all()


@router.delete("/mi-empresa/modulo-permiso/{permiso_id}", tags=["Permisos por Módulo"])
def quitar_permiso_modulo(
    permiso_id: uuid.UUID,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant_humano),
    _: UsuarioSaaS = Depends(requerir_gerencia_o_superadmin),
):
    permiso = db.exec(
        select(ModuloPermiso).where(ModuloPermiso.id == permiso_id, ModuloPermiso.tenant_id == context.tenant_id)
    ).first()
    if not permiso:
        raise HTTPException(status_code=404, detail="Permiso no encontrado.")

    permiso.activo = False
    db.add(permiso)
    db.commit()
    return {"mensaje": "Permiso desactivado."}
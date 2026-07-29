from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import func
import uuid

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant, TenantContext, get_usuario_actual
from app.core.rbac import requerir_gerencia_o_superadmin
from app.models.domain import UsuarioSaaS, RolUsuario, Tenant, Planta, UsuarioPlanta

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

class TenantUpdate(BaseModel):
    nombre: Optional[str] = None
    color_primario: Optional[str] = None
    logo_url: Optional[str] = None
    tolerancia_lento_pct: Optional[float] = None
    tolerancia_alerta_pct: Optional[float] = None

class UsuarioPlantaCreate(BaseModel):
    usuario_id: uuid.UUID
    planta_id: uuid.UUID

class UsuarioPlantaResponse(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID
    planta_id: uuid.UUID
    activo: bool


# ==========================================
# RUTAS DE PERFIL (FRONTEND BOOTSTRAP)
# ==========================================
@router.get("/usuarios/me", tags=["Perfil"])
def obtener_perfil_actual(usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    """Devuelve los datos del usuario logueado para que el Frontend arme el menú y los permisos."""
    return usuario_actual


# ==========================================
# GESTIÓN DE LA PROPIA EMPRESA (MI EMPRESA)
# ==========================================
@router.get("/mi-empresa/tenant", tags=["Gestión de Accesos (Empresa)"])
def obtener_mi_tenant(
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant)
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
    context: TenantContext = Depends(obtener_contexto_tenant)
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


# ==========================================
# ENDPOINTS DE USUARIOS (MI EMPRESA)
# ==========================================
@router.get("/mi-empresa/usuarios", response_model=List[dict])
def listar_usuarios_tenant(
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.SUPERVISOR]:
        raise HTTPException(status_code=403, detail="No tienes permisos para listar usuarios.")

    usuarios = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.tenant_id == context.tenant_id)).all()
    return [
        {
            "id": str(u.id), "auth0_id": u.auth0_id, "email": u.email,
            "nombre": u.nombre, "apellido": u.apellido, "rol": u.rol.value, "activo": u.activo
        } for u in usuarios
    ]

@router.post("/mi-empresa/usuarios", status_code=status.HTTP_201_CREATED)
def crear_usuario_tenant(
    payload: UsuarioCreate,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA]:
        raise HTTPException(status_code=403, detail="Solo Gerencia o SuperAdmin pueden crear usuarios.")

    if usuario_actual.rol == RolUsuario.GERENCIA and payload.rol == RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Infracción RBAC: Un Gerente no puede crear un SuperAdmin.")

    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == payload.email)).first():
        raise HTTPException(status_code=400, detail="El correo electrónico ya está registrado.")

    mock_auth0_id = payload.auth0_id or f"auth0|mock_{uuid.uuid4().hex[:8]}"
    nuevo_usuario = UsuarioSaaS(
        auth0_id=mock_auth0_id, tenant_id=context.tenant_id, email=payload.email,
        nombre=payload.nombre, apellido=payload.apellido, rol=payload.rol, activo=True
    )
    db.add(nuevo_usuario)
    db.commit()
    return {"mensaje": "Usuario creado con éxito", "id": str(nuevo_usuario.id)}

@router.patch("/mi-empresa/usuarios/{auth0_id_target}")
def actualizar_usuario(
    auth0_id_target: str,
    payload: UsuarioUpdate,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    usuario_target = db.exec(select(UsuarioSaaS).where(
        UsuarioSaaS.auth0_id == auth0_id_target, UsuarioSaaS.tenant_id == context.tenant_id
    )).first()

    if not usuario_target: raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if usuario_actual.auth0_id == auth0_id_target and payload.rol and payload.rol != usuario_target.rol:
        raise HTTPException(status_code=403, detail="No puedes auto-modificar tu rol.")
    if usuario_actual.rol == RolUsuario.GERENCIA and usuario_target.rol == RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="No tienes autoridad sobre un SuperAdmin.")

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
    stmt = select(
        Tenant.id, Tenant.nombre, Tenant.color_primario, Tenant.logo_url, func.count(UsuarioSaaS.id).label("total_usuarios")
    ).outerjoin(UsuarioSaaS, Tenant.id == UsuarioSaaS.tenant_id).group_by(Tenant.id)
    return [{"id": r.id, "nombre": r.nombre, "color_primario": r.color_primario, "logo_url": r.logo_url, "total_usuarios": r.total_usuarios} for r in db.exec(stmt).all()]

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

@router.patch("/superadmin/tenants/{tenant_id}/estado", tags=["SuperAdmin (Global)"])
def cambiar_estado_empresa(tenant_id: str, activo: bool, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    """Kill Switch: Activa o desactiva a TODOS los usuarios de una organización."""
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    
    usuarios = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.tenant_id == tenant_id)).all()
    if not usuarios: raise HTTPException(status_code=404, detail="Tenant no encontrado o sin usuarios.")
        
    for usuario in usuarios:
        usuario.activo = activo
        db.add(usuario)
        
    db.commit()
    return {"mensaje": f"Se ha {'activado' if activo else 'suspendido'} el acceso para {len(usuarios)} usuarios de {tenant_id}."}

@router.get("/superadmin/usuarios")
def listar_todos_los_usuarios_globales(db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    return db.exec(select(UsuarioSaaS)).all()

@router.post("/superadmin/usuarios", status_code=status.HTTP_201_CREATED)
def crear_usuario_b2b(nuevo_usuario: NuevoUsuarioSaaS, db: Session = Depends(get_session), usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == nuevo_usuario.email)).first():
        raise HTTPException(status_code=400, detail="Email ya registrado.")
    
    mock_auth0_id = f"auth0|mock_{uuid.uuid4().hex[:8]}"
    db_usuario = UsuarioSaaS(
        auth0_id=mock_auth0_id, tenant_id=nuevo_usuario.tenant_id, email=nuevo_usuario.email,
        rol=nuevo_usuario.rol, nombre=nuevo_usuario.nombre, apellido=nuevo_usuario.apellido, activo=True
    )
    db.add(db_usuario)
    db.commit()
    return {"mensaje": "Usuario B2B creado exitosamente.", "usuario": db_usuario}

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
    if usuario_actual.rol != RolUsuario.SUPERADMIN: raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")
    usuario_target = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario_target: raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if usuario_actual.auth0_id == auth0_id: raise HTTPException(status_code=400, detail="Operación suicida bloqueada.")

    db.delete(usuario_target)
    db.commit()
    return {"mensaje": "Usuario eliminado físicamente de la base de datos."}


# ==========================================
# RBAC GEOLOCALIZADO: ASIGNACIÓN USUARIO-PLANTA (Fase D.1)
# Aplica sólo a roles SUPERVISOR y OPERARIO; Gerencia/SuperAdmin ven
# todo el tenant sin necesitar asignación.
# ==========================================
@router.post("/mi-empresa/usuario-planta", response_model=UsuarioPlantaResponse, status_code=status.HTTP_201_CREATED, tags=["RBAC Geolocalizado"])
def asignar_usuario_a_planta(
    payload: UsuarioPlantaCreate,
    db: Session = Depends(get_session),
    context: TenantContext = Depends(obtener_contexto_tenant),
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
    context: TenantContext = Depends(obtener_contexto_tenant),
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
    context: TenantContext = Depends(obtener_contexto_tenant),
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
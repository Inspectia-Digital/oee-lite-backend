import base64
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel
from sqlmodel import Session, select
from sqlalchemy import text, func

from app.core.database import get_session
from app.core.auth import get_usuario_actual
from app.models.domain import UsuarioSaaS, RolUsuario, Tenant
from app.core.auth0_service import crear_usuario_en_auth0

router = APIRouter(prefix="/accesos")

# ==========================================
# GUARDIAS DE SEGURIDAD
# ==========================================

def get_superadmin(usuario: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario.rol != RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requiere privilegios de SuperAdmin.")
    return usuario

def get_admin_tenant(usuario: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.PRODUCCION]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requiere privilegios de Gerencia o Producción.")
    return usuario


# ==========================================
# SCHEMAS (PYDANTIC)
# ==========================================

class TenantCreate(BaseModel):
    id: str
    nombre: str

class TenantUpdate(BaseModel):
    nombre: Optional[str] = None
    color_primario: Optional[str] = None
    logo_url: Optional[str] = None
    
    # --- NUEVO: Permitimos que el gerente cambie el modo ---
    modo_asignacion_operarios: Optional[str] = None

class NuevoUsuarioSaaS(BaseModel):
    tenant_id: str
    email: str
    rol: RolUsuario
    nombre: str
    apellido: str

class ActualizarUsuario(BaseModel):
    rol: Optional[RolUsuario] = None
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    activo: Optional[bool] = None

class NuevoUsuarioInterno(BaseModel):
    email: str
    rol: RolUsuario  
    nombre: str
    apellido: str


# ==========================================
# RUTAS DE SETUP Y PERFIL
# ==========================================

@router.get("/migrar-db-urgente", tags=["SuperAdmin (Global)"])
def migrar_base_de_datos_urgente(db: Session = Depends(get_session)):
    """Ruta temporal SIN GUARDIA para romper la paradoja del huevo y la gallina"""
    try:
        db.exec(text("ALTER TABLE usuarios_saas ADD COLUMN IF NOT EXISTS nombre VARCHAR;"))
        db.exec(text("ALTER TABLE usuarios_saas ADD COLUMN IF NOT EXISTS apellido VARCHAR;"))
        db.commit()
        return {"status": "ok", "mensaje": "¡Columnas agregadas! Ya puedes usar el sistema."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en migración: {str(e)}")

@router.get("/setup/init-tenants", tags=["Setup"])
def inicializar_tenants_base(db: Session = Depends(get_session)):
    """Ruta temporal para crear los tenants fundacionales y asignar al SuperAdmin."""
    tenant_inspectia = db.exec(select(Tenant).where(Tenant.id == "inspectia_admin")).first()
    if not tenant_inspectia:
        tenant_inspectia = Tenant(id="inspectia_admin", nombre="Administrador InspectIA")
        db.add(tenant_inspectia)

    tenant_springwall = db.exec(select(Tenant).where(Tenant.id == "springwall")).first()
    if not tenant_springwall:
        tenant_springwall = Tenant(id="springwall", nombre="Springwall")
        db.add(tenant_springwall)

    db.commit()

    superadmins = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.rol == RolUsuario.SUPERADMIN)).all()
    admins_actualizados = 0
    for admin in superadmins:
        admin.tenant_id = "inspectia_admin"
        db.add(admin)
        admins_actualizados += 1

    db.commit()
    return {
        "status": "ok",
        "mensaje": "Tenants fundacionales inicializados correctamente",
        "tenants_creados": ["inspectia_admin", "springwall"],
        "superadmins_asignados": admins_actualizados
    }

@router.get("/usuarios/me", tags=["Perfil"])
def obtener_perfil_actual(usuario: UsuarioSaaS = Depends(get_usuario_actual)):
    """Devuelve los datos del usuario logueado según la base de datos de TYMEO."""
    return usuario


# ==========================================
# GESTIÓN DE TENANTS (SUPERADMIN)
# ==========================================

@router.get("/superadmin/tenants", tags=["SuperAdmin (Global)"])
def listar_todos_los_tenants(db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    """Lista todas las empresas cliente con el conteo de sus usuarios activos."""
    stmt = select(
        Tenant.id,
        Tenant.nombre,
        Tenant.color_primario,
        Tenant.logo_url,
        func.count(UsuarioSaaS.id).label("total_usuarios")
    ).outerjoin(UsuarioSaaS, Tenant.id == UsuarioSaaS.tenant_id).group_by(Tenant.id)
    
    resultados = db.exec(stmt).all()
    return [
        {
            "id": r.id, 
            "nombre": r.nombre, 
            "color_primario": r.color_primario,
            "logo_url": r.logo_url,
            "total_usuarios": r.total_usuarios
        } for r in resultados
    ]

@router.post("/superadmin/tenants", tags=["SuperAdmin (Global)"])
def crear_tenant(datos: TenantCreate, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    if db.exec(select(Tenant).where(Tenant.id == datos.id)).first():
        raise HTTPException(status_code=400, detail="El ID del tenant ya existe.")
        
    nuevo_tenant = Tenant(id=datos.id, nombre=datos.nombre)
    db.add(nuevo_tenant)
    db.commit()
    db.refresh(nuevo_tenant)
    return {"mensaje": "Empresa creada exitosamente", "tenant": nuevo_tenant}

@router.patch("/superadmin/tenants/{tenant_id}", tags=["SuperAdmin (Global)"])
def actualizar_tenant_global(tenant_id: str, datos: TenantUpdate, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    tenant_db = db.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
    if not tenant_db:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
        
    update_data = datos.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tenant_db, key, value)
        
    db.add(tenant_db)
    db.commit()
    db.refresh(tenant_db)
    return {"mensaje": "Empresa actualizada", "tenant": tenant_db}

@router.patch("/superadmin/tenants/{tenant_id}/estado", tags=["SuperAdmin (Global)"])
def cambiar_estado_empresa(tenant_id: str, activo: bool, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    """Kill Switch: Activa o desactiva a TODOS los usuarios de una organización."""
    usuarios = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.tenant_id == tenant_id)).all()
    if not usuarios:
        raise HTTPException(status_code=404, detail="Tenant no encontrado o sin usuarios.")
        
    for usuario in usuarios:
        usuario.activo = activo
        db.add(usuario)
        
    db.commit()
    return {"mensaje": f"Se ha {'activado' if activo else 'suspendido'} el acceso para {len(usuarios)} usuarios del tenant {tenant_id}."}


# ==========================================
# GESTIÓN GLOBAL DE USUARIOS (SUPERADMIN)
# ==========================================

@router.post("/superadmin/usuarios", tags=["SuperAdmin (Global)"], response_model=dict)
def crear_usuario_b2b(nuevo_usuario: NuevoUsuarioSaaS, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == nuevo_usuario.email)).first():
        raise HTTPException(status_code=400, detail="Este email ya está registrado en TYMEO.")
    
    auth0_id_generado = crear_usuario_en_auth0(nuevo_usuario.email)
    
    db_usuario = UsuarioSaaS(
        auth0_id=auth0_id_generado,
        tenant_id=nuevo_usuario.tenant_id,
        email=nuevo_usuario.email,
        rol=nuevo_usuario.rol,
        nombre=nuevo_usuario.nombre,
        apellido=nuevo_usuario.apellido
    )
    db.add(db_usuario)
    db.commit()
    db.refresh(db_usuario)
    return {"mensaje": f"Se ha enviado un correo a {db_usuario.email} para configurar su acceso.", "usuario": db_usuario}

@router.get("/superadmin/usuarios", tags=["SuperAdmin (Global)"])
def listar_todos_los_usuarios(db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    return db.exec(select(UsuarioSaaS)).all()

@router.patch("/superadmin/usuarios/{auth0_id}", tags=["SuperAdmin (Global)"])
def actualizar_usuario(auth0_id: str, datos: ActualizarUsuario, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario_db: raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    
    update_data = datos.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(usuario_db, key, value)
        
    db.add(usuario_db)
    db.commit()
    db.refresh(usuario_db)
    return {"mensaje": "Usuario actualizado exitosamente", "usuario": usuario_db}

@router.delete("/superadmin/usuarios/{auth0_id}", tags=["SuperAdmin (Global)"])
def eliminar_usuario_saas(auth0_id: str, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    """Eliminación física del usuario en la base de datos local."""
    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario_db:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        
    db.delete(usuario_db)
    db.commit()
    return {"mensaje": "Usuario eliminado correctamente de la base de datos local."}

@router.post("/superadmin/usuarios/{auth0_id}/reset-password", tags=["SuperAdmin (Global)"])
def forzar_reseteo_password(auth0_id: str, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario_db:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    
    # Pendiente: auth0_service.enviar_reset_password(usuario_db.email)
    return {"mensaje": f"Instrucción de reseteo registrada para {usuario_db.email}."}


# ==========================================
# GESTIÓN DE ACCESOS Y BRANDING (MI EMPRESA)
# ==========================================

@router.post("/mi-empresa/usuarios", tags=["Gestión de Accesos (Empresa)"])
def crear_usuario_interno(nuevo_usuario: NuevoUsuarioInterno, db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    if nuevo_usuario.rol not in [RolUsuario.SUPERVISOR, RolUsuario.OPERARIO]:
        raise HTTPException(status_code=403, detail="Solo puedes crear usuarios con rol de supervisor u operario.")
        
    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == nuevo_usuario.email)).first():
        raise HTTPException(status_code=400, detail="Este email ya está registrado.")
        
    auth0_id_generado = crear_usuario_en_auth0(nuevo_usuario.email)
    
    db_usuario = UsuarioSaaS(
        auth0_id=auth0_id_generado,
        tenant_id=admin_local.tenant_id,  
        email=nuevo_usuario.email,
        rol=nuevo_usuario.rol,
        nombre=nuevo_usuario.nombre,
        apellido=nuevo_usuario.apellido
    )
    db.add(db_usuario)
    db.commit()
    db.refresh(db_usuario)
    return {"mensaje": f"Se envió un correo a {db_usuario.email}.", "usuario": db_usuario}

@router.get("/mi-empresa/usuarios", tags=["Gestión de Accesos (Empresa)"])
def listar_usuarios_internos(db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    return db.exec(select(UsuarioSaaS).where(UsuarioSaaS.tenant_id == admin_local.tenant_id)).all()

@router.patch("/mi-empresa/usuarios/{auth0_id}", tags=["Gestión de Accesos (Empresa)"])
def actualizar_usuario_interno(auth0_id: str, datos: ActualizarUsuario, db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    usuario_db = db.exec(
        select(UsuarioSaaS).where(
            UsuarioSaaS.auth0_id == auth0_id,
            UsuarioSaaS.tenant_id == admin_local.tenant_id
        )
    ).first()
    
    if not usuario_db:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en tu empresa.")
        
    if datos.rol == RolUsuario.SUPERADMIN:
        if admin_local.rol != RolUsuario.SUPERADMIN:
            raise HTTPException(status_code=403, detail="No tienes permiso para asignar este rol.")
        
    update_data = datos.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(usuario_db, key, value)
        
    db.add(usuario_db)
    db.commit()
    db.refresh(usuario_db)
    return {"mensaje": "Personal actualizado exitosamente", "usuario": usuario_db}

@router.get("/mi-empresa/tenant", tags=["Gestión de Accesos (Empresa)"])
def obtener_mi_tenant(db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    tenant_db = db.exec(select(Tenant).where(Tenant.id == admin_local.tenant_id)).first()
    if not tenant_db:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    return tenant_db

@router.patch("/mi-empresa/tenant", tags=["Gestión de Accesos (Empresa)"])
def actualizar_mi_tenant(datos: TenantUpdate, db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    tenant_db = db.exec(select(Tenant).where(Tenant.id == admin_local.tenant_id)).first()
    if not tenant_db:
         raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    
    update_data = datos.model_dump(exclude_unset=True)
    update_data.pop("id", None) 
    
    for key, value in update_data.items():
        setattr(tenant_db, key, value)
        
    db.add(tenant_db)
    db.commit()
    db.refresh(tenant_db)
    return {"mensaje": "Branding actualizado", "tenant": tenant_db}

@router.post("/mi-empresa/tenant/logo", tags=["Gestión de Accesos (Empresa)"])
async def subir_mi_logo(file: UploadFile = File(...), db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo debe ser una imagen.")
        
    contenido = await file.read()
    if len(contenido) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen es demasiado grande. Máximo 2MB.")
        
    base64_encoded = base64.b64encode(contenido).decode("utf-8")
    logo_data_uri = f"data:{file.content_type};base64,{base64_encoded}"
    
    tenant_db = db.exec(select(Tenant).where(Tenant.id == admin_local.tenant_id)).first()
    if not tenant_db:
         raise HTTPException(status_code=404, detail="Tenant no encontrado.")
         
    tenant_db.logo_url = logo_data_uri
    db.add(tenant_db)
    db.commit()
    
    return {"mensaje": "Logo actualizado exitosamente", "logo_url": logo_data_uri}
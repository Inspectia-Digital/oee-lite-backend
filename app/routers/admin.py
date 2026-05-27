from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel
from sqlmodel import Session, select
from typing import Optional
from sqlalchemy import text
from app.core.database import get_session
from app.core.auth import get_usuario_actual
from app.models.domain import UsuarioSaaS, RolUsuario, Tenant
from app.core.auth0_service import crear_usuario_en_auth0 
import base64

router = APIRouter(prefix="/accesos")

# --- GUARDIAS ---
def get_superadmin(usuario: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario.rol != RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requiere privilegios de SuperAdmin.")
    return usuario

def get_admin_tenant(usuario: UsuarioSaaS = Depends(get_usuario_actual)):
    if usuario.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.PRODUCCION]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requiere privilegios de Gerencia o Producción.")
    return usuario

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
    
@router.get("/usuarios/me", tags=["Perfil"])
def obtener_perfil_actual(usuario: UsuarioSaaS = Depends(get_usuario_actual)):
    """
    Devuelve los datos del usuario logueado según la base de datos de TYMEO.
    El Frontend debe llamar a esta ruta apenas Auth0 confirma el login 
    para descubrir el rol real, el tenant_id y sus nombres.
    """
    return usuario

# --- MOLDES LIMPIOS Y ACTUALIZADOS ---

class TenantCreate(BaseModel):
    id: str
    nombre: str

class TenantUpdate(BaseModel):
    nombre: Optional[str] = None
    color_primario: Optional[str] = None
    # El logo_url se actualizará por un endpoint separado, pero lo permitimos aquí por si acaso
    logo_url: Optional[str] = None

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

# --- RUTAS ---
@router.post("/superadmin/usuarios", tags=["SuperAdmin (Global)"], response_model=dict)
def crear_usuario_b2b(
    nuevo_usuario: NuevoUsuarioSaaS,
    db: Session = Depends(get_session),
    admin: UsuarioSaaS = Depends(get_superadmin) 
):
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
def listar_todos_los_usuarios(
    db: Session = Depends(get_session), 
    admin: UsuarioSaaS = Depends(get_superadmin)
):
    """Lista todos los usuarios registrados en todas las empresas de la plataforma."""
    # Nota: Asegúrate de tener UsuarioSaaS importado arriba
    return db.exec(select(UsuarioSaaS)).all()

@router.patch("/superadmin/usuarios/{auth0_id}", tags=["SuperAdmin (Global)"])
def actualizar_usuario(
    auth0_id: str, 
    datos: ActualizarUsuario, 
    db: Session = Depends(get_session), 
    admin: UsuarioSaaS = Depends(get_superadmin)
):
    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_id)).first()
    if not usuario_db: raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    
    update_data = datos.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(usuario_db, key, value)
        
    db.add(usuario_db)
    db.commit()
    db.refresh(usuario_db)
    return {"mensaje": "Usuario actualizado exitosamente", "usuario": usuario_db}


@router.post("/mi-empresa/usuarios", tags=["Gestión de Accesos (Empresa)"])
def crear_usuario_interno(
    nuevo_usuario: NuevoUsuarioInterno,
    db: Session = Depends(get_session),
    admin_local: UsuarioSaaS = Depends(get_admin_tenant) 
):
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
    
    return {
        "mensaje": f"Se envió un correo a {db_usuario.email} para que ingrese a la plataforma.", 
        "usuario": db_usuario
    }

@router.get("/mi-empresa/usuarios", tags=["Gestión de Accesos (Empresa)"])
def listar_usuarios_internos(db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    return db.exec(select(UsuarioSaaS).where(UsuarioSaaS.tenant_id == admin_local.tenant_id)).all()

@router.patch("/mi-empresa/usuarios/{auth0_id}", tags=["Gestión de Accesos (Empresa)"])
def actualizar_usuario_interno(
    auth0_id: str,
    datos: ActualizarUsuario,
    db: Session = Depends(get_session),
    admin_local: UsuarioSaaS = Depends(get_admin_tenant)
):
    """Permite a Gerencia/Producción editar nombres, roles o dar de baja a SU personal."""
    
    # 1. Buscamos al usuario ASEGURANDO que pertenezca a la misma empresa
    usuario_db = db.exec(
        select(UsuarioSaaS).where(
            UsuarioSaaS.auth0_id == auth0_id,
            UsuarioSaaS.tenant_id == admin_local.tenant_id # 🔒 Candado B2B
        )
    ).first()
    
    if not usuario_db:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en tu empresa.")
        
# 2. Evitamos que un gerente asigne o mantenga el rol de SuperAdmin
    if datos.rol == RolUsuario.SUPERADMIN:
        # El único que puede manipular el rol superadmin es el propio superadmin
        if admin_local.rol != RolUsuario.SUPERADMIN:
            raise HTTPException(
                status_code=403, 
                detail="No tienes permiso para asignar este rol."
            )
        
    # 3. Aplicamos los cambios (solo los campos que el frontend haya enviado)
    update_data = datos.model_dump(exclude_unset=True) 
    for key, value in update_data.items():
        setattr(usuario_db, key, value)
        
    db.add(usuario_db)
    db.commit()
    db.refresh(usuario_db)
    
    return {"mensaje": "Personal actualizado exitosamente", "usuario": usuario_db}

@router.get("/setup/init-tenants", tags=["Setup"])
def inicializar_tenants_base(db: Session = Depends(get_session)):
    """
    Ruta temporal para crear los tenants fundacionales y asignar al SuperAdmin.
    """
    # 1. Crear el Tenant de InspectIA (Tu Cuartel General)
    tenant_inspectia = db.exec(select(Tenant).where(Tenant.id == "inspectia_admin")).first()
    if not tenant_inspectia:
        tenant_inspectia = Tenant(id="inspectia_admin", nombre="Administrador InspectIA")
        db.add(tenant_inspectia)

    # 2. Crear el Tenant de Springwall (Tu primer cliente)
    tenant_springwall = db.exec(select(Tenant).where(Tenant.id == "springwall")).first()
    if not tenant_springwall:
        tenant_springwall = Tenant(id="springwall", nombre="Springwall")
        db.add(tenant_springwall)

    db.commit() # Guardamos las empresas primero

    # 3. Buscar a todos los SuperAdmins (tu usuario) y asignarlos a InspectIA
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

# ==========================================
# GESTIÓN DE TENANTS (SUPERADMIN)
# ==========================================

@router.get("/superadmin/tenants", tags=["SuperAdmin (Global)"])
def listar_todos_los_tenants(db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    """Lista todas las empresas cliente registradas en el sistema."""
    from app.models.domain import Tenant
    return db.exec(select(Tenant)).all()

@router.post("/superadmin/tenants", tags=["SuperAdmin (Global)"])
def crear_tenant(datos: TenantCreate, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    """Crea una nueva empresa/tenant."""
    from app.models.domain import Tenant
    
    if db.exec(select(Tenant).where(Tenant.id == datos.id)).first():
        raise HTTPException(status_code=400, detail="El ID del tenant ya existe.")
        
    nuevo_tenant = Tenant(id=datos.id, nombre=datos.nombre)
    db.add(nuevo_tenant)
    db.commit()
    db.refresh(nuevo_tenant)
    return {"mensaje": "Empresa creada exitosamente", "tenant": nuevo_tenant}

@router.patch("/superadmin/tenants/{tenant_id}", tags=["SuperAdmin (Global)"])
def actualizar_tenant_global(tenant_id: str, datos: TenantUpdate, db: Session = Depends(get_session), admin: UsuarioSaaS = Depends(get_superadmin)):
    """Permite al SuperAdmin editar cualquier empresa."""
    from app.models.domain import Tenant
    
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

import base64
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlmodel import Session, select
# Mueve este import al principio de tu archivo admin.py
from app.models.domain import Tenant, UsuarioSaaS 
from app.core.database import get_session
# Asumo que tienes tus esquemas TenantUpdate definidos arriba

# ==========================================
# GESTIÓN DE BRANDING (MI EMPRESA)
# ==========================================

@router.get("/mi-empresa/tenant", tags=["Gestión de Accesos (Empresa)"])
def obtener_mi_tenant(db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    """Devuelve los datos de branding de la empresa del usuario logueado."""
    tenant_db = db.exec(select(Tenant).where(Tenant.id == admin_local.tenant_id)).first()
    
    if not tenant_db:
        raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    return tenant_db

@router.patch("/mi-empresa/tenant", tags=["Gestión de Accesos (Empresa)"])
def actualizar_mi_tenant(datos: TenantUpdate, db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    """Permite a Gerencia actualizar el nombre o color de su empresa."""
    tenant_db = db.exec(select(Tenant).where(Tenant.id == admin_local.tenant_id)).first()
    
    if not tenant_db:
         raise HTTPException(status_code=404, detail="Tenant no encontrado.")
    
    update_data = datos.model_dump(exclude_unset=True)
    
    # Bloqueamos que intenten cambiar el ID del tenant maliciosamente
    update_data.pop("id", None) 
    
    for key, value in update_data.items():
        setattr(tenant_db, key, value)
        
    db.add(tenant_db)
    db.commit()
    db.refresh(tenant_db)
    return {"mensaje": "Branding actualizado", "tenant": tenant_db}

@router.post("/mi-empresa/tenant/logo", tags=["Gestión de Accesos (Empresa)"])
async def subir_mi_logo(file: UploadFile = File(...), db: Session = Depends(get_session), admin_local: UsuarioSaaS = Depends(get_admin_tenant)):
    """Sube un logo y lo guarda como Base64 en la base de datos (Solución MVP)."""
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo debe ser una imagen.")
        
    contenido = await file.read()
    
    # Restringir tamaño a ~2MB para no saturar la base de datos
    if len(contenido) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen es demasiado grande. Máximo 2MB.")
        
    # Convertir a Base64
    base64_encoded = base64.b64encode(contenido).decode("utf-8")
    logo_data_uri = f"data:{file.content_type};base64,{base64_encoded}"
    
    tenant_db = db.exec(select(Tenant).where(Tenant.id == admin_local.tenant_id)).first()
    
    if not tenant_db:
         raise HTTPException(status_code=404, detail="Tenant no encontrado.")
         
    tenant_db.logo_url = logo_data_uri
    
    db.add(tenant_db)
    db.commit()
    
    return {"mensaje": "Logo actualizado exitosamente", "logo_url": logo_data_uri}
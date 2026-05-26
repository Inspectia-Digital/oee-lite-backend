from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select
from typing import Optional
from sqlalchemy import text
from app.core.database import get_session
from app.core.auth import get_usuario_actual
from app.models.domain import UsuarioSaaS, RolUsuario
from app.core.auth0_service import crear_usuario_en_auth0 

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
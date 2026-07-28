from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import func
import uuid

from app.core.database import get_session
from app.core.auth import obtener_contexto_tenant, TenantContext, get_usuario_actual
from app.models.domain import UsuarioSaaS, RolUsuario, Tenant

router = APIRouter(prefix="/accesos", tags=["Administración SaaS y RBAC"])

# ==========================================
# MOLDES (SCHEMAS)
# ==========================================
class UsuarioCreate(BaseModel):
    email: str
    nombre: str
    apellido: str
    rol: RolUsuario
    auth0_id: Optional[str] = None  # Si el front no interactúa con Auth0 Management API aún

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

# ==========================================
# RUTAS DE PERFIL (FRONTEND BOOTSTRAP)
# ==========================================
@router.get("/usuarios/me", tags=["Perfil"])
def obtener_perfil_actual(usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    """Devuelve los datos del usuario logueado para que el Frontend arme el menú y los permisos."""
    return usuario_actual

# ==========================================
# ENDPOINTS DE USUARIOS (CONTEXTO / MODO DIOS)
# ==========================================

@router.get("/mi-empresa/usuarios", response_model=List[dict])
def listar_usuarios_tenant(
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    """Lista los usuarios. Si es SuperAdmin en Modo Dios, lista los del tenant impersonado."""
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.SUPERVISOR]:
        raise HTTPException(status_code=403, detail="No tienes permisos para listar usuarios.")

    usuarios = db.exec(
        select(UsuarioSaaS).where(UsuarioSaaS.tenant_id == context.tenant_id)
    ).all()

    return [
        {
            "id": str(u.id),
            "auth0_id": u.auth0_id,
            "email": u.email,
            "nombre": u.nombre,
            "apellido": u.apellido,
            "rol": u.rol.value,
            "activo": u.activo
        } for u in usuarios
    ]

@router.post("/mi-empresa/usuarios", status_code=status.HTTP_201_CREATED)
def crear_usuario_tenant(
    payload: UsuarioCreate,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    """Crea un usuario en la empresa actual. Aplica reglas estrictas de RBAC."""
    # 1. Reglas de Negocio de Creación
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA]:
        raise HTTPException(status_code=403, detail="Solo Gerencia o SuperAdmin pueden crear usuarios.")

    if usuario_actual.rol == RolUsuario.GERENCIA and payload.rol == RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Infracción RBAC: Un Gerente no puede crear un SuperAdmin.")

    # 2. Verificación de Duplicados a nivel global
    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == payload.email)).first():
        raise HTTPException(status_code=400, detail="El correo electrónico ya está registrado en el sistema.")

    # 3. Creación
    mock_auth0_id = payload.auth0_id or f"auth0|mock_{uuid.uuid4().hex[:8]}"

    nuevo_usuario = UsuarioSaaS(
        auth0_id=mock_auth0_id,
        tenant_id=context.tenant_id,  # 🟢 Inyecta el tenant_id (ej. green_mills)
        email=payload.email,
        nombre=payload.nombre,
        apellido=payload.apellido,
        rol=payload.rol,
        activo=True
    )
    
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)
    
    return {"mensaje": "Usuario creado con éxito", "id": str(nuevo_usuario.id)}

@router.patch("/mi-empresa/usuarios/{auth0_id_target}")
def actualizar_usuario(
    auth0_id_target: str,
    payload: UsuarioUpdate,
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    """Actualiza a un usuario asegurando que pertenezca al Tenant correcto."""
    usuario_target = db.exec(
        select(UsuarioSaaS).where(
            UsuarioSaaS.auth0_id == auth0_id_target,
            UsuarioSaaS.tenant_id == context.tenant_id # 🟢 Aislamiento estricto
        )
    ).first()

    if not usuario_target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta organización.")

    if usuario_actual.auth0_id == auth0_id_target and payload.rol and payload.rol != usuario_target.rol:
        raise HTTPException(status_code=403, detail="No puedes auto-modificar tu rol.")

    if usuario_actual.rol == RolUsuario.GERENCIA and usuario_target.rol == RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="No tienes autoridad sobre un SuperAdmin.")

    if payload.nombre is not None: usuario_target.nombre = payload.nombre
    if payload.apellido is not None: usuario_target.apellido = payload.apellido
    if payload.activo is not None: usuario_target.activo = payload.activo
    if payload.rol is not None: usuario_target.rol = payload.rol

    db.add(usuario_target)
    db.commit()
    return {"mensaje": "Usuario actualizado con éxito"}


# ==========================================
# ENDPOINTS SUPERADMIN (SaaS Core Global)
# ==========================================

@router.get("/superadmin/tenants")
def listar_todos_los_tenants(
    db: Session = Depends(get_session), 
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)
):
    """Lista todas las empresas cliente para el Dropdown del SuperAdmin."""
    if usuario_actual.rol != RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")

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


@router.get("/superadmin/usuarios")
def listar_todos_los_usuarios_globales(
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)
):
    """Endpoint exclusivo para el panel maestro de InspectIA."""
    if usuario_actual.rol != RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Acceso denegado. Exclusivo InspectIA Core.")
        
    usuarios = db.exec(select(UsuarioSaaS)).all()
    return usuarios


@router.post("/superadmin/usuarios", status_code=status.HTTP_201_CREATED)
def crear_usuario_b2b(
    nuevo_usuario: NuevoUsuarioSaaS, 
    db: Session = Depends(get_session), 
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)
):
    """Crea un usuario en cualquier Tenant desde el Panel Central SaaS."""
    if usuario_actual.rol != RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Exclusivo InspectIA Core.")

    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == nuevo_usuario.email)).first():
        raise HTTPException(status_code=400, detail="Este email ya está registrado en el sistema.")
    
    mock_auth0_id = f"auth0|mock_{uuid.uuid4().hex[:8]}"
    
    db_usuario = UsuarioSaaS(
        auth0_id=mock_auth0_id,
        tenant_id=nuevo_usuario.tenant_id,
        email=nuevo_usuario.email,
        rol=nuevo_usuario.rol,
        nombre=nuevo_usuario.nombre,
        apellido=nuevo_usuario.apellido,
        activo=True
    )
    db.add(db_usuario)
    db.commit()
    db.refresh(db_usuario)
    return {"mensaje": "Usuario B2B creado exitosamente.", "usuario": db_usuario}
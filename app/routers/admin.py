from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import List, Optional
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


# ==========================================
# ENDPOINTS DE USUARIOS (Con Modo Dios)
# ==========================================

@router.get("/mi-empresa/usuarios", response_model=List[dict])
def listar_usuarios_tenant(
    db: Session = Depends(get_session),
    usuario_actual: UsuarioSaaS = Depends(get_usuario_actual),
    context: TenantContext = Depends(obtener_contexto_tenant)
):
    """
    Lista los usuarios. Si es SuperAdmin en Modo Dios, lista los del tenant impersonado.
    """
    # 1. Firewall de Roles: Solo niveles jerárquicos altos pueden ver la lista completa
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.SUPERVISOR]:
        raise HTTPException(status_code=403, detail="No tienes permisos para listar usuarios.")

    # 2. Búsqueda aislada mediante el Contexto
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
    """
    Crea un usuario. Aplica reglas estrictas de RBAC y respeta el Modo Dios.
    """
    # 1. Reglas de Negocio de Creación
    if usuario_actual.rol not in [RolUsuario.SUPERADMIN, RolUsuario.GERENCIA]:
        raise HTTPException(status_code=403, detail="Solo Gerencia o SuperAdmin pueden crear usuarios.")

    if usuario_actual.rol == RolUsuario.GERENCIA and payload.rol == RolUsuario.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Infracción RBAC: Un Gerente no puede crear un SuperAdmin.")

    # 2. Verificación de Duplicados a nivel global (Los emails SaaS son únicos)
    if db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == payload.email)).first():
        raise HTTPException(status_code=400, detail="El correo electrónico ya está registrado en el sistema.")

    # 3. Creación (Mock del auth0_id temporal si no llega del Frontend)
    mock_auth0_id = payload.auth0_id or f"auth0|mock_{uuid.uuid4().hex[:8]}"

    nuevo_usuario = UsuarioSaaS(
        auth0_id=mock_auth0_id,
        tenant_id=context.tenant_id,  # 🟢 La Magia: Se inyecta 'green_mills' si estás impersonando
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
    """
    Actualiza a un usuario asegurando que pertenezca al Tenant correcto.
    """
    usuario_target = db.exec(
        select(UsuarioSaaS).where(
            UsuarioSaaS.auth0_id == auth0_id_target,
            UsuarioSaaS.tenant_id == context.tenant_id # 🟢 Aislamiento estricto
        )
    ).first()

    if not usuario_target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta organización.")

    # Regla: Nadie puede modificarse a sí mismo para subir de privilegios
    if usuario_actual.auth0_id == auth0_id_target and payload.rol and payload.rol != usuario_target.rol:
        raise HTTPException(status_code=403, detail="No puedes auto-modificar tu rol.")

    # Regla: Gerencia no puede degradar ni modificar a un SuperAdmin
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
# RUTAS DE PERFIL (FRONTEND BOOTSTRAP)
# ==========================================
@router.get("/usuarios/me", tags=["Perfil"])
def obtener_perfil_actual(usuario_actual: UsuarioSaaS = Depends(get_usuario_actual)):
    """Devuelve los datos del usuario logueado para que el Frontend arme el menú y los permisos."""
    return usuario_actual

# ==========================================
# ENDPOINTS SUPERADMIN (SaaS Core)
# ==========================================
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
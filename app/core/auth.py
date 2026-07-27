import json
import os
from urllib.request import urlopen
from typing import Optional
from functools import lru_cache

from fastapi import Depends, HTTPException, status, Query, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from jose import jwt
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.domain import UsuarioSaaS, RolUsuario, Planta, Tenant

# ==========================================
# CONFIGURACIÓN DE AUTH0
# ==========================================
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-bzem6wpwmlr14eha.us.auth0.com") 
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://api.tymeo.com")
ALGORITHMS = ["RS256"]

token_auth_scheme = HTTPBearer()

@lru_cache(maxsize=1)
def get_auth0_jwks():
    """
    Obtiene y CACHEA las llaves públicas de Auth0 en RAM.
    Evita hacer requests externos por cada API call y previene baneos por Rate Limit.
    """
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    try:
        return json.loads(urlopen(url).read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error de red al contactar Auth0 JWKS: {str(e)}")

def verificar_token_auth0(credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme)):
    """Valida la firma criptográfica del token contra Auth0 usando cache en RAM."""
    token = credentials.credentials
    
    # Leemos desde la memoria RAM (0 milisegundos, 0 requests externos)
    jwks = get_auth0_jwks()

    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Formato de token inválido")

    rsa_key = {}
    for key in jwks["keys"]:
        if key["kid"] == unverified_header.get("kid"):
            rsa_key = {
                "kty": key["kty"], 
                "kid": key["kid"], 
                "use": key["use"], 
                "n": key["n"], 
                "e": key["e"]
            }
            
    if rsa_key:
        try:
            payload = jwt.decode(
                token, 
                rsa_key, 
                algorithms=ALGORITHMS, 
                audience=AUTH0_AUDIENCE, 
                issuer=f"https://{AUTH0_DOMAIN}/"
            )
            return payload
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
            
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No se encontró llave pública.")


def get_usuario_actual(payload: dict = Depends(verificar_token_auth0), db: Session = Depends(get_session)) -> UsuarioSaaS:
    """Busca al usuario autenticado en nuestra base de datos para saber su 'tenant_id' y sus permisos."""
    auth0_sub = payload.get("sub")
    
    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    
    if not usuario_db:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Usuario autenticado, pero no tiene una empresa asignada en InspectIA OS."
        )
        
    if not usuario_db.activo:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo.")
        
    return usuario_db


# ==========================================
# GOBERNANZA MULTI-TENANT (Control de Módulos)
# ==========================================
def requerir_modulo(modulo_requerido: str):
    """Dependencia de FastAPI para verificar módulos contratados por el Tenant."""
    def dependencia_verificadora(
        usuario: UsuarioSaaS = Depends(get_usuario_actual),
        db: Session = Depends(get_session)
    ) -> str:
        tenant_id = usuario.tenant_id
        tenant = db.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
        
        if not tenant or not tenant.activo:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La empresa (Tenant) no existe o se encuentra inactiva."
            )

        modulos_str = tenant.modulos_contratados or ""
        modulos_permitidos = [m.strip().lower() for m in modulos_str.split(",") if m.strip()]
        
        if modulo_requerido.lower() not in modulos_permitidos:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Acceso denegado. El módulo '{modulo_requerido}' no está incluido."
            )

        return tenant_id
    return dependencia_verificadora


# ==========================================
# CONTEXTO INSPECTIA OS (Multi-Planta & Modo Dios)
# ==========================================
class TenantContext(BaseModel):
    tenant_id: str
    sub_tenant_id: Optional[str] = None
    is_superadmin: bool = False

def obtener_contexto_tenant(
    usuario: UsuarioSaaS = Depends(get_usuario_actual),
    x_sub_tenant_id: Optional[str] = Header(None, alias="X-Sub-Tenant-Id"),
    impersonate_tenant: Optional[str] = Query(None, alias="tenant_id"),
    db: Session = Depends(get_session)
) -> TenantContext:
    is_superadmin = (usuario.rol == RolUsuario.SUPERADMIN)
    tenant_activo = usuario.tenant_id

    if impersonate_tenant:
        if not is_superadmin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Solo SuperAdmin puede usar el Modo Dios (?tenant_id=...)"
            )
        tenant_activo = impersonate_tenant

    if x_sub_tenant_id:
        planta = db.exec(
            select(Planta)
            .where(Planta.id == x_sub_tenant_id, Planta.tenant_id == tenant_activo)
        ).first()
        
        if not planta:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La planta seleccionada no existe o no pertenece a esta empresa."
            )

    return TenantContext(
        tenant_id=tenant_activo,
        sub_tenant_id=x_sub_tenant_id,
        is_superadmin=is_superadmin
    )

def obtener_tenant_aislado(
    tenant_impersonado: Optional[str] = Query(None, alias="tenant_id"),
    usuario: UsuarioSaaS = Depends(get_usuario_actual)
) -> str:
    if usuario.rol == RolUsuario.SUPERADMIN and tenant_impersonado:
        return tenant_impersonado
    return usuario.tenant_id
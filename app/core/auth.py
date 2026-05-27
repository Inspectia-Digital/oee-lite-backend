import json
import os
from urllib.request import urlopen
from typing import Optional

from fastapi import Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.domain import UsuarioSaaS, RolUsuario

# ==========================================
# CONFIGURACIÓN DE AUTH0
# ==========================================
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-bzem6wpwmlr14eha.us.auth0.com") 
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://api.tymeo.com")
ALGORITHMS = ["RS256"]

token_auth_scheme = HTTPBearer()

def verificar_token_auth0(credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme)):
    """Valida la firma criptográfica del token contra Auth0."""
    token = credentials.credentials
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    
    try:
        jwks = json.loads(urlopen(url).read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error de conexión con Auth0: {str(e)}")

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
            return payload # Retorna los datos crudos del token
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
            
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No se encontró llave pública.")


def get_usuario_actual(payload: dict = Depends(verificar_token_auth0), db: Session = Depends(get_session)) -> UsuarioSaaS:
    """
    Busca al usuario autenticado en nuestra base de datos para saber su 'tenant_id' y sus permisos.
    """
    auth0_sub = payload.get("sub")
    
    # Buscamos en nuestra tabla si este usuario está registrado en TYMEO
    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    
    if not usuario_db:
        # Si entra alguien que Auth0 reconoce pero nosotros no, le prohibimos el acceso
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Usuario autenticado, pero no tiene una empresa asignada en TYMEO."
        )
        
    if not usuario_db.activo:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo.")
        
    return usuario_db


# ==========================================
# AISLAMIENTO Y "MODO DIOS" (IMPERSONATION)
# ==========================================

def obtener_tenant_aislado(
    tenant_impersonado: Optional[str] = Query(
        None, 
        alias="tenant_id", 
        description="Solo SuperAdmin: Permite ver los datos de otra empresa"
    ),
    usuario: UsuarioSaaS = Depends(get_usuario_actual)
) -> str:
    """
    Resuelve el tenant_id garantizando el aislamiento estricto de datos B2B.
    Si un SuperAdmin envía ?tenant_id=X en la URL, el sistema adopta esa identidad para soporte.
    Para el resto de los mortales, ignora cualquier parámetro y fuerza el tenant real del usuario.
    """
    if usuario.rol == RolUsuario.SUPERADMIN and tenant_impersonado:
        return tenant_impersonado
        
    # Candado irrompible: forzamos el tenant_id real del usuario
    return usuario.tenant_id
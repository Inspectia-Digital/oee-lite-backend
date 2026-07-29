import json
import logging
from urllib.request import urlopen
from typing import Optional
from functools import lru_cache
import uuid
from fastapi import Depends, HTTPException, status, Query, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from jose import jwt
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import get_session
from app.models.domain import UsuarioSaaS, RolUsuario, Planta, Tenant, EstadoTenant

# Habilitamos el logger para evitar que los errores queden invisibles en Cloud Run
logger = logging.getLogger(__name__)

# ==========================================
# CONFIGURACIÓN DE AUTH0
# ==========================================
# En producción, settings.py ya exige AUTH0_DOMAIN/AUTH0_AUDIENCE explícitos
# (falla el arranque si faltan). Fuera de producción (development/staging),
# se mantiene un fallback de transición para no romper logins existentes
# en entornos que todavía no seteen estas variables explícitamente; se
# loguea un warning fuerte para que se detecte y corrija.
_FALLBACK_AUTH0_DOMAIN = "dev-bzem6wpwmlr14eha.us.auth0.com"
_FALLBACK_AUTH0_AUDIENCE = "https://api.tymeo.com"

_auth0_domain_raw = settings.AUTH0_DOMAIN
_auth0_audience_raw = settings.AUTH0_AUDIENCE

if not _auth0_domain_raw and not settings.is_production:
    logger.warning(
        "AUTH0_DOMAIN no está seteado como variable de entorno; usando fallback de "
        "transición '%s'. Configurar explícitamente antes de promover a producción.",
        _FALLBACK_AUTH0_DOMAIN,
    )
    _auth0_domain_raw = _FALLBACK_AUTH0_DOMAIN

if not _auth0_audience_raw and not settings.is_production:
    logger.warning(
        "AUTH0_AUDIENCE no está seteado como variable de entorno; usando fallback de "
        "transición '%s'. Configurar explícitamente antes de promover a producción.",
        _FALLBACK_AUTH0_AUDIENCE,
    )
    _auth0_audience_raw = _FALLBACK_AUTH0_AUDIENCE

AUTH0_DOMAIN = _auth0_domain_raw.replace("https://", "").replace("http://", "").rstrip("/")
AUTH0_AUDIENCE = _auth0_audience_raw

ALGORITHMS = ["RS256"]

token_auth_scheme = HTTPBearer()

@lru_cache(maxsize=1)
def get_auth0_jwks():
    """Obtiene y CACHEA las llaves públicas de Auth0 en RAM."""
    if not AUTH0_DOMAIN:
        logger.error("AUTH0_DOMAIN no está configurado.")
        raise HTTPException(status_code=503, detail="Autenticación no disponible temporalmente.")

    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    try:
        return json.loads(urlopen(url, timeout=settings.AUTH0_JWKS_TIMEOUT_SECONDS).read())
    except Exception as e:
        # Log detallado interno, pero respuesta pública sanitizada (sin URL/stack trace)
        logger.error(f"Falla crítica contactando Auth0 en {url}: {str(e)}")
        raise HTTPException(status_code=503, detail="Autenticación no disponible temporalmente.")


def verificar_token_auth0(credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme)):
    """Valida la firma criptográfica del token contra Auth0 usando cache en RAM."""
    token = credentials.credentials
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
            logger.warning(f"Token JWT rechazado: {str(e)}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o expirado.")
            
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
                detail="Solo SuperAdmin puede usar el Modo Dios"
            )
        tenant_activo = impersonate_tenant

    if x_sub_tenant_id:
        try:
            # 1. Intentamos leerlo como UUID real (Producción)
            valid_uuid = uuid.UUID(x_sub_tenant_id)
            planta = db.exec(
                select(Planta).where(Planta.id == valid_uuid, Planta.tenant_id == tenant_activo)
            ).first()
        except ValueError:
            # 2. Si el front manda basura o un mock (ej: "pilar"), buscamos por nombre sin crashear
            planta = db.exec(
                select(Planta).where(Planta.nombre.ilike(f"%{x_sub_tenant_id}%"), Planta.tenant_id == tenant_activo)
            ).first()
        
        if not planta:
            # Si no existe la planta mock, limpiamos el header para que cargue la vista global
            x_sub_tenant_id = None
        else:
            x_sub_tenant_id = str(planta.id)

    return TenantContext(
        tenant_id=tenant_activo,
        sub_tenant_id=x_sub_tenant_id,
        is_superadmin=is_superadmin
    )

def obtener_tenant_aislado(
    tenant_impersonado: Optional[str] = Query(None, alias="tenant_id"),
    usuario: UsuarioSaaS = Depends(get_usuario_actual)
) -> str:
    """(Legacy) Mantenido por retrocompatibilidad con endpoints viejos de Tymeo."""
    if usuario.rol == RolUsuario.SUPERADMIN and tenant_impersonado:
        return tenant_impersonado
    return usuario.tenant_id


# ==========================================
# SUSPENSIÓN DE TENANT (Fase D.2)
# ==========================================
def verificar_no_suspension_total(tenant: Optional[Tenant]) -> None:
    """Corta Edge/M2M sólo si el tenant está en SUSPENSION_TOTAL."""
    if tenant and tenant.estado == EstadoTenant.SUSPENSION_TOTAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La empresa se encuentra en suspensión total.",
        )


def _verificar_acceso_humano_habilitado(tenant: Optional[Tenant]) -> None:
    """Corta endpoints humanos si el tenant está UI_SUSPENDIDA o en SUSPENSION_TOTAL."""
    if tenant and tenant.estado in (EstadoTenant.UI_SUSPENDIDA, EstadoTenant.SUSPENSION_TOTAL):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El acceso humano de esta empresa está suspendido.",
        )


def obtener_contexto_tenant_humano(
    context: TenantContext = Depends(obtener_contexto_tenant),
    db: Session = Depends(get_session),
) -> TenantContext:
    """Igual que obtener_contexto_tenant, pero corta con 403 si la empresa
    tiene el acceso humano suspendido (UI_SUSPENDIDA o SUSPENSION_TOTAL).
    Usar en endpoints humanos (dashboards, configuración, administración).
    No usar en endpoints de Edge/M2M: ahí la UI_SUSPENDIDA no debe cortar
    la ingesta (ver obtener_contexto_tenant_edge / obtener_tenant_aislado_edge).
    """
    tenant = db.get(Tenant, context.tenant_id)
    _verificar_acceso_humano_habilitado(tenant)
    return context


def obtener_contexto_tenant_edge(
    context: TenantContext = Depends(obtener_contexto_tenant),
    db: Session = Depends(get_session),
) -> TenantContext:
    """Para endpoints de Edge/M2M: sólo corta en SUSPENSION_TOTAL, nunca en
    UI_SUSPENDIDA (el hardware debe poder seguir mandando datos)."""
    tenant = db.get(Tenant, context.tenant_id)
    verificar_no_suspension_total(tenant)
    return context


def obtener_tenant_aislado_edge(
    tenant_id: str = Depends(obtener_tenant_aislado),
    db: Session = Depends(get_session),
) -> str:
    """Variante Edge/M2M de obtener_tenant_aislado: sólo corta en SUSPENSION_TOTAL."""
    tenant = db.get(Tenant, tenant_id)
    verificar_no_suspension_total(tenant)
    return tenant_id
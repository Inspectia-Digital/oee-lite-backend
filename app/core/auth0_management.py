"""Cliente mínimo de la Auth0 Management API (Fase J).

Usa una app M2M separada de la de login humano, autorizada sólo con el
scope create:user_tickets -- alcanza para generar un link de cambio de
password sin necesitar update:users (más privilegio del que hace falta).
"""
import logging
import secrets
from typing import Optional

import requests
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.auth import AUTH0_DOMAIN

logger = logging.getLogger(__name__)


def _obtener_token_management_api() -> str:
    if not settings.AUTH0_MGMT_CLIENT_ID or not settings.AUTH0_MGMT_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth0 Management API no está configurada (faltan AUTH0_MGMT_CLIENT_ID/SECRET).",
        )
    try:
        resp = requests.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            json={
                "client_id": settings.AUTH0_MGMT_CLIENT_ID,
                "client_secret": settings.AUTH0_MGMT_CLIENT_SECRET,
                "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
                "grant_type": "client_credentials",
            },
            timeout=settings.AUTH0_MGMT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except requests.RequestException as e:
        logger.error(f"Falla obteniendo token de Auth0 Management API: {e}")
        raise HTTPException(status_code=503, detail="No se pudo autenticar contra Auth0 Management API.")


def crear_ticket_cambio_password(auth0_id: str) -> str:
    """Genera un ticket de cambio de password (link de un solo uso) para el
    usuario indicado. Devuelve la URL del ticket."""
    token = _obtener_token_management_api()
    try:
        resp = requests.post(
            f"https://{AUTH0_DOMAIN}/api/v2/tickets/password-change",
            headers={"Authorization": f"Bearer {token}"},
            json={"user_id": auth0_id},
            timeout=settings.AUTH0_MGMT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()["ticket"]
    except requests.RequestException as e:
        logger.error(f"Falla creando ticket de reset-password para {auth0_id}: {e}")
        raise HTTPException(status_code=503, detail="No se pudo generar el link de cambio de password.")


def obtener_email_usuario_auth0(auth0_id: str) -> Optional[str]:
    """Resuelve el email real de un usuario de Auth0 vía la Management API
    (Fase EU, auto-provisioning de tenant en el primer login) -- el access
    token humano NO trae email (sólo `sub`, ver verificar_token_auth0 en
    auth.py), así que hace falta esta llamada aparte.

    Best-effort A PROPÓSITO, a diferencia de crear_ticket_cambio_password:
    si faltan credenciales, si Auth0 no responde, o si el usuario no tiene
    email (login sin conexión de email configurada) -- nunca levanta
    excepción. El auto-provisioning tiene que poder seguir funcionando
    igual sin este dato (degrada a un nombre de tenant genérico).

    Requiere el scope read:users en el M2M client de Management API (hoy
    sólo tiene create:user_tickets, configurado fuera del repo en el
    dashboard de Auth0) -- si no está habilitado, la llamada falla con
    403/401 y esta función devuelve None sin romper nada."""
    try:
        token = _obtener_token_management_api()
    except HTTPException:
        return None
    try:
        resp = requests.get(
            f"https://{AUTH0_DOMAIN}/api/v2/users/{auth0_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "email", "include_fields": "true"},
            timeout=settings.AUTH0_MGMT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("email")
    except requests.RequestException as e:
        logger.warning(f"No se pudo resolver el email de Auth0 para {auth0_id}: {e}")
        return None


def crear_usuario_auth0(email: str, nombre: str, apellido: str) -> str:
    """Fase EV: crea un usuario REAL en Auth0 cuando se lo invita desde el
    panel -- reemplaza al placeholder `auth0|mock_xxxx` que se usaba antes
    (ver domain de RegistroInvitado en admin.py). Password aleatoria
    DESCARTABLE: nunca se usa para loguearse -- el llamador genera un
    ticket de cambio de password (crear_ticket_cambio_password) inmediato
    después de esto, ese es el único link real que la persona recibe.

    A diferencia de obtener_email_usuario_auth0, esto NO es best-effort:
    levanta la excepción real (HTTPException 503, o requests.HTTPError si
    Auth0 rechaza el alta -- ej. el email ya existe ahí) para que el
    llamador decida el fallback explícitamente (hoy: degradar al mock de
    toda la vida, ver crear_usuario_tenant/crear_usuario_b2b en admin.py).

    Requiere el scope create:users en el M2M client de Management API
    (configurado fuera del repo, dashboard de Auth0) -- si no está,
    Auth0 devuelve 403 acá mismo."""
    token = _obtener_token_management_api()
    password_descartable = secrets.token_urlsafe(24) + "Aa1!"
    resp = requests.post(
        f"https://{AUTH0_DOMAIN}/api/v2/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": email,
            "given_name": nombre,
            "family_name": apellido,
            "name": f"{nombre} {apellido}".strip(),
            "connection": settings.AUTH0_DB_CONNECTION_NAME,
            "password": password_descartable,
            "email_verified": False,
        },
        timeout=settings.AUTH0_MGMT_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["user_id"]

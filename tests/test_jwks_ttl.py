"""Fase K (auditoría QA #12): la caché de JWKS tiene TTL en vez de vivir
para siempre (@lru_cache sin vencimiento), y un kid desconocido dispara un
refresco inmediato en vez de esperar el TTL o un reinicio de instancia."""
import base64
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core import auth as auth_module


def _jwks_con_kid(kid: str) -> dict:
    return {"keys": [{"kty": "RSA", "kid": kid, "use": "sig", "n": "abc", "e": "AQAB"}]}


def _token_con_kid(kid: str) -> str:
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(b'{"sub":"test"}').rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}.firma-invalida"


@pytest.fixture(autouse=True)
def _limpiar_cache_jwks():
    auth_module._jwks_cache.clear()
    yield
    auth_module._jwks_cache.clear()


def test_jwks_cache_evita_llamadas_repetidas():
    respuesta_falsa = MagicMock()
    respuesta_falsa.read.return_value = json.dumps(_jwks_con_kid("kid-1")).encode()

    with patch("app.core.auth.urlopen", return_value=respuesta_falsa) as mock_urlopen:
        auth_module.get_auth0_jwks()
        auth_module.get_auth0_jwks()
        assert mock_urlopen.call_count == 1


def test_jwks_forzar_refresco_ignora_cache():
    respuesta_falsa = MagicMock()
    respuesta_falsa.read.return_value = json.dumps(_jwks_con_kid("kid-1")).encode()

    with patch("app.core.auth.urlopen", return_value=respuesta_falsa) as mock_urlopen:
        auth_module.get_auth0_jwks()
        auth_module.get_auth0_jwks(forzar_refresco=True)
        assert mock_urlopen.call_count == 2


def test_kid_desconocido_dispara_un_refresco_automatico():
    """El kid de la caché vieja no matchea; se refresca una vez, se
    encuentra en el JWKS nuevo, y el flujo llega a intentar decodificar
    (falla ahí porque el token es falso, pero eso prueba que sí reintentó
    -- si no reintentara, cortaría antes con 'No se encontró llave pública')."""
    jwks_viejo = _jwks_con_kid("kid-viejo")
    jwks_nuevo = _jwks_con_kid("kid-nuevo")
    mock_jwks = MagicMock(side_effect=[jwks_viejo, jwks_nuevo])

    class _CredencialesFalsas:
        credentials = _token_con_kid("kid-nuevo")

    with patch("app.core.auth.get_auth0_jwks", mock_jwks):
        with pytest.raises(HTTPException) as exc_info:
            auth_module.verificar_token_auth0(credentials=_CredencialesFalsas())

    assert mock_jwks.call_count == 2
    mock_jwks.assert_any_call(forzar_refresco=True)
    # Llegó a intentar decodificar (token inválido/expirado), no se rindió
    # antes con "No se encontró llave pública".
    assert exc_info.value.detail == "Token inválido o expirado."

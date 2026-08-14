"""Fase CA (auditoría de robustez, batch 3): taxonomía de errores para
los endpoints de la terminal física (scans.py + auth_m2m.py).

Diseño: el `detail` de cada HTTPException se queda EXACTAMENTE igual
que antes (texto libre en español) -- varios de esos strings ya son un
contrato de facto con integraciones externas (ej. el prefijo literal
"EVENTO_FUERA_DE_ORDEN:" que ya usa el gateway de Green Mills, ver
scans.py). Cambiar `detail` a un objeto habría roto esos consumidores y
a la vez el `unwrapError` genérico de apiClient.ts, que asume que
`detail` es siempre un string.

El código estructurado viaja aparte, en el header de respuesta
`X-Error-Code` -- aditivo, cero riesgo para cualquier consumidor
existente (PLC o frontend) que sólo lea `detail`. El frontend de la
terminal lo lee opcionalmente para distinguir el tratamiento de la
pantalla de error (ver TerminalPage.tsx).
"""
from enum import Enum

from fastapi import HTTPException


class ErrorCode(str, Enum):
    CREDENCIAL_FALTANTE = "CREDENCIAL_FALTANTE"
    CREDENCIAL_FORMATO_INVALIDO = "CREDENCIAL_FORMATO_INVALIDO"
    CREDENCIAL_INVALIDA = "CREDENCIAL_INVALIDA"
    CREDENCIAL_REVOCADA = "CREDENCIAL_REVOCADA"
    CREDENCIAL_EXPIRADA = "CREDENCIAL_EXPIRADA"
    ESTACION_NO_AUTORIZADA = "ESTACION_NO_AUTORIZADA"
    OPERARIO_NO_ENCONTRADO = "OPERARIO_NO_ENCONTRADO"
    TURNO_NO_ENCONTRADO = "TURNO_NO_ENCONTRADO"
    SESION_NO_ENCONTRADA = "SESION_NO_ENCONTRADA"
    EVENTO_ID_CONFLICTO = "EVENTO_ID_CONFLICTO"
    EVENTO_FUERA_DE_ORDEN = "EVENTO_FUERA_DE_ORDEN"
    TIMESTAMP_FUERA_DE_RANGO = "TIMESTAMP_FUERA_DE_RANGO"
    CANTIDAD_RECHAZADA_INVALIDA = "CANTIDAD_RECHAZADA_INVALIDA"
    LIMITE_DE_INTENTOS = "LIMITE_DE_INTENTOS"


def error_terminal(status_code: int, code: ErrorCode, detail: str) -> HTTPException:
    """`detail` sin cambios respecto al string que ya se mandaba --
    `code` viaja en el header `X-Error-Code`, nunca en el body."""
    return HTTPException(status_code=status_code, detail=detail, headers={"X-Error-Code": code.value})

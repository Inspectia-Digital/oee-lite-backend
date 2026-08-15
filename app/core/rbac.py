"""Dependencias RBAC reutilizables (Fase D).

El código legacy (admin.py, configuracion.py) repite `if usuario.rol not in [...]`
en cada endpoint; ese refactor se hace en Fase D.3. Todo el código NUEVO de
Fase D en adelante debe usar estas dependencias en vez de repetir el patrón.
"""
from fastapi import Depends, HTTPException, status

from app.core.auth import get_usuario_actual
from app.models.domain import RolUsuario, UsuarioSaaS


def requerir_roles(*roles_permitidos: RolUsuario):
    """Factory de dependencia: exige que el usuario autenticado tenga uno de los roles dados."""
    def _dependencia(usuario: UsuarioSaaS = Depends(get_usuario_actual)) -> UsuarioSaaS:
        if usuario.rol not in roles_permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tenés permisos para realizar esta acción.",
            )
        return usuario
    return _dependencia


requerir_gerencia_o_superadmin = requerir_roles(RolUsuario.SUPERADMIN, RolUsuario.GERENCIA)
requerir_superadmin = requerir_roles(RolUsuario.SUPERADMIN)

# Fase DC (pedido del usuario, coherencia de RBAC): gestión de SUPERVISORES
# (crear/editar/desactivar el registro de la persona, vincularla a un
# usuario web) -- antes sólo Gerencia/SuperAdmin, ahora también Producción.
# Deliberadamente sin RolUsuario.SUPERVISOR: un supervisor no debe poder
# gestionar el alta de otros supervisores.
requerir_gerencia_produccion_o_superadmin = requerir_roles(
    RolUsuario.SUPERADMIN, RolUsuario.GERENCIA, RolUsuario.PRODUCCION,
)

"""Auto-provisioning de Tenant en el primer login (Fase EU).

Antes de esto, un usuario nuevo de Auth0 (Google u otro método) sin
ninguna fila UsuarioSaaS asociada quedaba en un 403 duro y sin ninguna
vía de autoservicio -- confirmado con el usuario, tenía que resolverlo
asociándose a mano cada vez. Este módulo reemplaza ese 403 por un alta
automática de Tenant + primer usuario, deliberadamente SIN módulos
contratados (a diferencia del default "tymeo" del modelo) -- el usuario
ve una pantalla de "solicitá acceso" (frontend, SinModulosScreen) hasta
que un SuperAdmin le asigne un módulo a mano desde Billing.

Vive en un módulo propio (no en auth.py) para evitar un import circular
real: auth0_management.py ya importa AUTH0_DOMAIN desde auth.py -- si
auth.py importara este módulo y este módulo importara auth0_management,
el ciclo sería auth.py -> onboarding.py -> auth0_management.py -> auth.py."""
import logging
import uuid

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.auth0_management import obtener_email_usuario_auth0
from app.models.domain import RolUsuario, Tenant, UsuarioSaaS

logger = logging.getLogger(__name__)

# Mismo prefijo que admin.py::crear_usuario_b2b/crear_usuario_tenant usan
# para el auth0_id placeholder de un usuario invitado a mano desde el
# panel (todavía no se logueó nunca, no se le conoce el sub real).
_PREFIJO_AUTH0_ID_INVITADO = "auth0|mock_"


def provisionar_tenant_y_usuario(auth0_sub: str, db: Session) -> UsuarioSaaS:
    """Fase EU.2 (hallazgo real de uso: BPS-Demo/Cecilia): antes de crear
    un Tenant nuevo, busca si esta persona ya fue INVITADA a mano desde
    el panel (Configuración -> Usuarios / Panel SaaS -> Nuevo usuario) --
    esos endpoints crean la fila con un auth0_id INVENTADO
    (auth0|mock_xxxx) porque en ese momento no se conoce el sub real de
    Auth0 (no hay forma de saberlo antes del primer login de esa
    persona). Sin este matching, el primer login real de un usuario
    invitado no encontraba nada por auth0_id y terminaba
    auto-provisionando un tenant FANTASMA nuevo y vacío, desconectado
    del tenant/rol/módulos que el admin ya había armado a mano -- el
    usuario veía "sin módulos" a pesar de que sí le habían asignado uno.

    Si hay match por email con una fila "auth0|mock_*": se LINKEA esa
    fila existente (se le pisa el auth0_id por el real) en vez de crear
    un tenant nuevo. Sólo si no hay invitación pendiente se cae al
    alta de Tenant nuevo (self-signup real, Fase EU original).

    email es best-effort (Auth0 Management API, ver auth0_management.py)
    -- sin email no hay forma de buscar la invitación, se cae directo al
    alta de tenant nuevo (mismo comportamiento que antes de este fix).

    Carrera (Fase K, mismo criterio que scans.py): dos requests
    concurrentes con el mismo auth0_sub nuevo pueden intentar provisionar
    (o linkear) en simultáneo -- auth0_id es UNIQUE en UsuarioSaaS, así
    que la segunda escritura choca con IntegrityError; se hace rollback y
    se devuelve la fila que ganó la otra transacción, en vez de duplicar."""
    email = obtener_email_usuario_auth0(auth0_sub)

    if email:
        usuario_invitado = db.exec(
            select(UsuarioSaaS).where(
                func.lower(UsuarioSaaS.email) == email.lower(),
                UsuarioSaaS.auth0_id.like(f"{_PREFIJO_AUTH0_ID_INVITADO}%"),
            )
        ).first()
        if usuario_invitado:
            usuario_invitado.auth0_id = auth0_sub
            db.add(usuario_invitado)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                logger.info(f"Carrera linkeando invitación para auth0_sub={auth0_sub}: otra request ya lo linkeó, reusando.")
                usuario_existente = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
                if not usuario_existente:
                    raise
                return usuario_existente
            db.refresh(usuario_invitado)
            logger.info(
                f"Auto-provisioning: usuario invitado {usuario_invitado.id} "
                f"(tenant={usuario_invitado.tenant_id}) linkeado a auth0_sub={auth0_sub} (email={email})."
            )
            return usuario_invitado

    nombre_tenant = f"Empresa de {email}" if email else f"Empresa sin nombre ({auth0_sub})"

    tenant = Tenant(
        id=str(uuid.uuid4()),
        nombre=nombre_tenant,
        modulos_contratados="",  # explícito: pisa el default "tymeo" del modelo (domain.py)
    )
    db.add(tenant)
    db.flush()  # asegura el INSERT de Tenant antes del de UsuarioSaaS (FK tenant_id -> tenants_saas.id)

    usuario = UsuarioSaaS(
        auth0_id=auth0_sub,
        tenant_id=tenant.id,
        email=email,
        rol=RolUsuario.GERENCIA,
        activo=True,
    )
    db.add(usuario)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info(f"Carrera de auto-provisioning para auth0_sub={auth0_sub}: otra request ya lo creó, reusando.")
        usuario_existente = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
        if not usuario_existente:
            # No debería pasar (la única causa de IntegrityError acá es la
            # unicidad de auth0_id) -- si pasa, es un error real distinto,
            # no lo escondemos detrás de un None silencioso.
            raise
        return usuario_existente

    db.refresh(usuario)
    logger.info(f"Auto-provisioning: nuevo tenant {tenant.id} + usuario {usuario.id} (auth0_sub={auth0_sub}).")
    return usuario

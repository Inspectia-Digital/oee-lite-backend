"""Fase EU: auto-provisioning de Tenant en el primer login (PRD Auth0 prod
nuevo). Antes de esto, un auth0_sub sin UsuarioSaaS asociado quedaba en un
403 duro sin ninguna vía de autoservicio -- ver docstring de
app/core/onboarding.py.

Mecanismo de auth acá: verificar_token_auth0 se overridea DIRECTO (no
get_usuario_actual, que es lo que autenticar_como() de conftest.py hace) --
es un nivel más abajo en la cadena de dependencias, necesario para simular
un auth0_sub que todavía NO tiene fila de UsuarioSaaS (autenticar_como()
sólo puede simular un usuario que YA existe, por diseño)."""
import threading
import uuid
from unittest.mock import patch

from sqlmodel import select

from app.core.auth import verificar_token_auth0
from app.models.domain import Tenant, UsuarioSaaS
from main import app


def _sub_unico(prefijo: str) -> str:
    """DB persistente sin rollback por test (conftest.py) -- un literal
    fijo colisiona con la unique constraint de auth0_id en reruns."""
    return f"google-oauth2|{prefijo}-{uuid.uuid4().hex[:8]}"


def _simular_login_nuevo(auth0_sub: str):
    """Overridea verificar_token_auth0 para simular un JWT ya validado de
    un auth0_sub que todavía no tiene UsuarioSaaS -- mismo criterio que
    autenticar_como(), un nivel más abajo."""
    app.dependency_overrides[verificar_token_auth0] = lambda: {"sub": auth0_sub}


def test_primer_login_auto_provisiona_tenant_y_usuario(client, db):
    auth0_sub = _sub_unico("nuevo-sin-tenant")
    _simular_login_nuevo(auth0_sub)

    r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["rol"] == "gerencia"

    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    assert usuario_db is not None
    assert usuario_db.activo is True
    assert usuario_db.rol.value == "gerencia"

    tenant_db = db.get(Tenant, usuario_db.tenant_id)
    assert tenant_db is not None
    # El punto central del pedido: modulos_contratados vacío EXPLÍCITO,
    # no el default "tymeo" del modelo.
    assert tenant_db.modulos_contratados == ""


def test_auto_provisioning_usa_email_de_management_api(client, db):
    auth0_sub = _sub_unico("con-email")
    _simular_login_nuevo(auth0_sub)

    with patch("app.core.onboarding.obtener_email_usuario_auth0", return_value="persona@empresa-nueva.com"):
        r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200

    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    assert usuario_db.email == "persona@empresa-nueva.com"
    tenant_db = db.get(Tenant, usuario_db.tenant_id)
    assert "persona@empresa-nueva.com" in tenant_db.nombre


def test_auto_provisioning_sin_email_no_falla(client, db):
    auth0_sub = _sub_unico("sin-email")
    _simular_login_nuevo(auth0_sub)

    with patch("app.core.onboarding.obtener_email_usuario_auth0", return_value=None):
        r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200

    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    assert usuario_db.email is None
    tenant_db = db.get(Tenant, usuario_db.tenant_id)
    assert tenant_db.nombre  # no vacío, nombre genérico igual


def test_management_api_email_lookup_mockeada(client, db, monkeypatch):
    """Wiring real de obtener_email_usuario_auth0 (no sólo el mock a nivel
    de módulo del test anterior) -- mismo patrón que
    test_logo_reset_password.py::test_reset_password_exitoso_con_management_api_mockeada."""
    monkeypatch.setattr("app.core.config.settings.AUTH0_MGMT_CLIENT_ID", "test_client_id")
    monkeypatch.setattr("app.core.config.settings.AUTH0_MGMT_CLIENT_SECRET", "test_secret")

    auth0_sub = _sub_unico("wiring-real")

    class _RespuestaFalsa:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def _post_falso(url, **kwargs):
        assert "/oauth/token" in url
        return _RespuestaFalsa({"access_token": "fake-mgmt-token"})

    def _get_falso(url, **kwargs):
        assert auth0_sub in url
        assert kwargs["headers"]["Authorization"] == "Bearer fake-mgmt-token"
        return _RespuestaFalsa({"email": "real@wiring-test.com"})

    _simular_login_nuevo(auth0_sub)
    with patch("app.core.auth0_management.requests.post", side_effect=_post_falso), \
         patch("app.core.auth0_management.requests.get", side_effect=_get_falso):
        r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200

    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    assert usuario_db.email == "real@wiring-test.com"


def test_carrera_dos_requests_concurrentes_mismo_auth0_sub_crea_un_solo_tenant(client, db):
    auth0_sub = _sub_unico("carrera-onboarding")
    _simular_login_nuevo(auth0_sub)

    resultados = []

    def _disparar():
        r = client.get("/accesos/usuarios/me")
        resultados.append(r.status_code)

    hilos = [threading.Thread(target=_disparar) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert all(sc == 200 for sc in resultados), resultados

    usuarios_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).all()
    assert len(usuarios_db) == 1, "la carrera duplicó el UsuarioSaaS -- debería reusar el que ganó la otra transacción"

    tenants_creados = db.exec(select(Tenant).where(Tenant.id == usuarios_db[0].tenant_id)).all()
    assert len(tenants_creados) == 1


def test_usuario_existente_no_reprovisiona(client, db, gerente_a, tenant_a):
    # /usuarios/me ya no depende de get_usuario_actual (autenticar_como no
    # aplica acá) -- depende de resolver_o_provisionar_usuario_actual, un
    # nivel más abajo, así que hay que simular el JWT con el auth0_id real
    # de un usuario YA existente para probar que no lo reprovisiona.
    _simular_login_nuevo(gerente_a.auth0_id)
    r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200
    data = r.json()
    assert data["tenant_id"] == tenant_a

    # Ningún Tenant/UsuarioSaaS nuevo -- sigue siendo exactamente el mismo.
    usuarios_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == gerente_a.auth0_id)).all()
    assert len(usuarios_db) == 1
    assert usuarios_db[0].id == gerente_a.id


def test_usuario_inactivo_sigue_bloqueado(client, db, tenant_a):
    from app.models.domain import RolUsuario

    inactivo = UsuarioSaaS(auth0_id=_sub_unico("inactivo-test"), tenant_id=tenant_a, rol=RolUsuario.SUPERVISOR, activo=False)
    db.add(inactivo)
    db.commit()
    db.refresh(inactivo)

    _simular_login_nuevo(inactivo.auth0_id)
    r = client.get("/accesos/usuarios/me")
    assert r.status_code == 403
    assert r.json()["detail"] == "Usuario inactivo."

    # Sigue siendo el mismo, no se creó un tenant/usuario nuevo por error.
    usuarios_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == inactivo.auth0_id)).all()
    assert len(usuarios_db) == 1


# ---------- Fase EU.2: linkear una invitación previa (auth0|mock_*) por email ----------
# Hallazgo real de uso (BPS-Demo/Cecilia): un admin invita a alguien desde
# el panel (crear_usuario_b2b/crear_usuario_tenant, admin.py) -- esos
# endpoints crean la fila con un auth0_id inventado porque todavía no se
# conoce el sub real. Sin este matching, el primer login real de esa
# persona no encontraba nada por auth0_id y auto-provisionaba un tenant
# fantasma vacío en vez de conectarla al tenant/rol que el admin ya armó.

def _crear_usuario_invitado(db, tenant_id, email, rol=None):
    from app.models.domain import RolUsuario
    invitado = UsuarioSaaS(
        auth0_id=f"auth0|mock_{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        email=email,
        rol=rol or RolUsuario.GERENCIA,
        activo=True,
    )
    db.add(invitado)
    db.commit()
    db.refresh(invitado)
    return invitado


def test_login_real_linkea_usuario_invitado_por_email(client, db, tenant_a):
    email = f"cecilia-{uuid.uuid4().hex[:8]}@empresa-real.com"
    invitado = _crear_usuario_invitado(db, tenant_a, email)

    auth0_sub_real = _sub_unico("login-real-de-invitado")
    _simular_login_nuevo(auth0_sub_real)

    with patch("app.core.onboarding.obtener_email_usuario_auth0", return_value=email):
        r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200, r.text
    data = r.json()
    # Se linkeó a la fila YA existente -- mismo tenant y mismo rol que
    # armó el admin, no un tenant fantasma nuevo.
    assert data["tenant_id"] == tenant_a
    assert data["id"] == str(invitado.id)
    assert data["rol"] == "gerencia"

    db.refresh(invitado)
    assert invitado.auth0_id == auth0_sub_real

    # Ningún tenant nuevo de por medio.
    usuarios_con_ese_email = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == email)).all()
    assert len(usuarios_con_ese_email) == 1


def test_login_real_linkea_invitacion_sin_importar_mayusculas_en_email(client, db, tenant_a):
    email = f"Cecilia.SanMartin-{uuid.uuid4().hex[:8]}@Empresa.com"
    invitado = _crear_usuario_invitado(db, tenant_a, email)

    auth0_sub_real = _sub_unico("login-mayus")
    _simular_login_nuevo(auth0_sub_real)

    with patch("app.core.onboarding.obtener_email_usuario_auth0", return_value=email.lower()):
        r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200

    db.refresh(invitado)
    assert invitado.auth0_id == auth0_sub_real


def test_sin_invitacion_previa_sigue_provisionando_tenant_fantasma_normal(client, db):
    """Regresión: si NO hay ninguna fila auth0|mock_* con ese email, el
    comportamiento sigue siendo el de Fase EU original -- tenant nuevo,
    sin módulos."""
    email = f"nadie-invito-{uuid.uuid4().hex[:8]}@nuevo.com"
    auth0_sub = _sub_unico("sin-invitacion")
    _simular_login_nuevo(auth0_sub)

    with patch("app.core.onboarding.obtener_email_usuario_auth0", return_value=email):
        r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200

    usuario_db = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    tenant_db = db.get(Tenant, usuario_db.tenant_id)
    assert tenant_db.modulos_contratados == ""
    assert tenant_db.nombre != ""


def test_invitacion_con_email_distinto_no_matchea_por_error(client, db, tenant_a):
    """Un usuario invitado con OTRO email no debe linkearse -- evita falsos
    positivos que le darían a alguien el tenant/rol de otra persona."""
    email_invitado = f"otra-persona-{uuid.uuid4().hex[:8]}@empresa.com"
    _crear_usuario_invitado(db, tenant_a, email_invitado)

    email_del_que_loguea = f"no-tiene-nada-que-ver-{uuid.uuid4().hex[:8]}@otra.com"
    auth0_sub = _sub_unico("email-no-coincide")
    _simular_login_nuevo(auth0_sub)

    with patch("app.core.onboarding.obtener_email_usuario_auth0", return_value=email_del_que_loguea):
        r = client.get("/accesos/usuarios/me")
    assert r.status_code == 200
    data = r.json()
    # Le tocó un tenant NUEVO propio, no el de la invitación ajena.
    assert data["tenant_id"] != tenant_a


def test_carrera_linkeando_la_misma_invitacion_no_duplica(client, db, tenant_a):
    email = f"carrera-invitacion-{uuid.uuid4().hex[:8]}@empresa.com"
    invitado = _crear_usuario_invitado(db, tenant_a, email)

    auth0_sub_real = _sub_unico("carrera-linkeo")
    _simular_login_nuevo(auth0_sub_real)

    resultados = []

    def _disparar():
        with patch("app.core.onboarding.obtener_email_usuario_auth0", return_value=email):
            r = client.get("/accesos/usuarios/me")
        resultados.append(r.status_code)

    hilos = [threading.Thread(target=_disparar) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert all(sc == 200 for sc in resultados), resultados

    # expire_all(): las dos requests linkearon la fila desde sus PROPIAS
    # sesiones (otro hilo, otra conexión) -- sin esto, `db` (la sesión de
    # este test, que ya tenía a `invitado` cacheado desde _crear_usuario_
    # invitado) devolvería el objeto viejo de su identity map en vez de
    # releer la fila real de la base.
    db.expire_all()
    usuarios_con_ese_email = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == email)).all()
    assert len(usuarios_con_ese_email) == 1
    assert usuarios_con_ese_email[0].id == invitado.id
    assert usuarios_con_ese_email[0].auth0_id == auth0_sub_real

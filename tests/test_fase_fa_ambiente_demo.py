"""Fase FA (PRD Demo/Partners/Marketplace/Soporte/Planes): Ambiente Demo
autoservicio para el equipo comercial. Cubre el CRUD (SuperAdmin
exclusivo), la estructura generada por industria, autofrenado tras
MAX_HORAS_SIMULACION y limpieza automática de demos expiradas --
pedido explícito del usuario: nada de tenants fantasma acumulándose ni
simulaciones corriendo para siempre (performance/costo de infra).

El envío real de scans simulados (_enviar_scan) hace un POST HTTP a
127.0.0.1 -- no hay servidor real escuchando bajo pytest (TestClient no
bindea un puerto), así que se mockea httpx.post para testear la lógica
de selección/autofrenado sin depender de un server vivo. La
verificación end-to-end real (evento realmente insertado) se hizo
contra dev tras el deploy, no acá."""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlmodel import select

from app.core import demo_simulador
from app.models.domain import DemoCredencialSimulador, Estacion, Linea, MaestroSKU, Tenant, Turno
from tests.conftest import autenticar_como


def test_no_superadmin_no_puede_crear_demo(client, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post("/admin/demo/crear", json={"nombre": "Demo X", "industria": "textil"})
    assert r.status_code == 403


def test_crear_demo_industria_invalida_devuelve_422(client, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/admin/demo/crear", json={"nombre": "Demo X", "industria": "no_existe"})
    assert r.status_code == 422


def test_crear_demo_arma_estructura_completa_segun_industria(client, db, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/admin/demo/crear", json={"nombre": "Demo Textil SA", "industria": "textil"})
    assert r.status_code == 201, r.text
    demo = r.json()
    assert demo["industria_demo"] == "textil"
    assert demo["demo_simulando_desde"] is None
    assert demo["demo_expira_at"] is not None

    tenant_db = db.exec(select(Tenant).where(Tenant.id == demo["id"])).first()
    assert tenant_db.es_demo is True

    lineas = db.exec(select(Linea).where(Linea.tenant_id == demo["id"])).all()
    assert len(lineas) == 1
    assert "Tejeduría" in lineas[0].nombre

    estaciones = db.exec(select(Estacion).where(Estacion.tenant_id == demo["id"])).all()
    assert len(estaciones) == 1

    turnos = db.exec(select(Turno).where(Turno.tenant_id == demo["id"])).all()
    assert len(turnos) == 1

    skus = db.exec(select(MaestroSKU).where(MaestroSKU.tenant_id == demo["id"])).all()
    assert len(skus) == 2  # template "textil" tiene 2 SKUs

    # Credencial M2M interna emitida por cada estación -- necesaria para
    # que el simulador pueda operarla (ver demo_simulador._tick_simulacion).
    credenciales = db.exec(select(DemoCredencialSimulador).where(DemoCredencialSimulador.tenant_id == demo["id"])).all()
    assert len(credenciales) == len(estaciones)
    assert "." in credenciales[0].credencial_completa  # formato key_id.secret


def test_crear_demo_tamano_mediana_duplica_lineas_y_estaciones(client, db, superadmin):
    autenticar_como(superadmin.id)
    r = client.post("/admin/demo/crear", json={"nombre": "Demo Grande", "industria": "alimenticia", "tamano": "mediana"})
    assert r.status_code == 201
    demo_id = r.json()["id"]

    assert len(db.exec(select(Linea).where(Linea.tenant_id == demo_id)).all()) == 2
    assert len(db.exec(select(Estacion).where(Estacion.tenant_id == demo_id)).all()) == 4  # 2 líneas x 2 estaciones


def test_iniciar_y_detener_simulacion(client, db, superadmin):
    autenticar_como(superadmin.id)
    demo_id = client.post("/admin/demo/crear", json={"nombre": "Demo", "industria": "automotriz"}).json()["id"]

    r = client.post(f"/admin/demo/{demo_id}/simular/iniciar", json={"velocidad": "rapida"})
    assert r.status_code == 200
    assert r.json()["demo_simulando_desde"] is not None
    assert r.json()["demo_velocidad"] == "rapida"

    r = client.post(f"/admin/demo/{demo_id}/simular/detener")
    assert r.status_code == 200
    assert r.json()["demo_simulando_desde"] is None


def test_listar_demos_no_incluye_tenants_normales(client, db, superadmin, tenant_a):
    autenticar_como(superadmin.id)
    client.post("/admin/demo/crear", json={"nombre": "Demo", "industria": "metalurgica"})

    r = client.get("/admin/demo/")
    assert r.status_code == 200
    ids = [d["id"] for d in r.json()]
    assert tenant_a not in ids  # tenant_a es un tenant normal (es_demo=False), no debe aparecer


def test_reiniciar_demo_borra_eventos_generados(client, db, superadmin):
    autenticar_como(superadmin.id)
    demo_id = client.post("/admin/demo/crear", json={"nombre": "Demo", "industria": "textil"}).json()["id"]
    estacion = db.exec(select(Estacion).where(Estacion.tenant_id == demo_id)).first()

    from app.models.domain import LiteEventoProduccion
    db.add(LiteEventoProduccion(
        tenant_id=demo_id, id_estacion=str(estacion.id), timestamp=datetime.utcnow(),
        unidades_procesadas=1, estado="OPTIMO", event_id=uuid.uuid4(),
    ))
    db.commit()
    assert len(db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.tenant_id == demo_id)).all()) == 1

    r = client.post(f"/admin/demo/{demo_id}/reiniciar")
    assert r.status_code == 204
    assert len(db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.tenant_id == demo_id)).all()) == 0
    # La estructura (línea/estación/SKUs) NO se toca -- sólo lo transaccional.
    assert db.exec(select(Estacion).where(Estacion.tenant_id == demo_id)).first() is not None


def test_eliminar_demo_borra_el_tenant_completo(client, db, superadmin):
    autenticar_como(superadmin.id)
    demo_id = client.post("/admin/demo/crear", json={"nombre": "Demo", "industria": "textil"}).json()["id"]

    r = client.delete(f"/admin/demo/{demo_id}")
    assert r.status_code == 204
    assert db.exec(select(Tenant).where(Tenant.id == demo_id)).first() is None
    assert db.exec(select(Estacion).where(Estacion.tenant_id == demo_id)).all() == []


def test_no_se_puede_gestionar_un_tenant_que_no_es_demo(client, superadmin, tenant_a):
    """Los endpoints de /admin/demo/{id} sólo operan sobre tenants
    es_demo=true -- evita que alguien apague/borre un cliente real por
    error de UUID."""
    autenticar_como(superadmin.id)
    r = client.post(f"/admin/demo/{tenant_a}/simular/iniciar", json={"velocidad": "normal"})
    assert r.status_code == 404
    r = client.delete(f"/admin/demo/{tenant_a}")
    assert r.status_code == 404


# ---------- Scheduler: autofrenado y limpieza (llamado directo, sin hilo real) ----------

def test_autofrenado_apaga_simulacion_tras_max_horas(db, superadmin):
    # _tick_simulacion() opera sobre TODOS los tenants demo con
    # simulación activa en la base compartida de test (no sólo el de
    # este test) -- se verifica el efecto puntual sobre "tenant"
    # (demo_simulando_desde vuelve a None), no una ausencia global de
    # llamadas a httpx.post (otro test podría dejar su propia demo
    # corriendo y generar sus propios envíos reales en este mismo tick).
    tenant = demo_simulador.crear_estructura_demo(db, "Demo Autofrenado", "textil")
    tenant.demo_simulando_desde = datetime.utcnow() - timedelta(hours=demo_simulador.MAX_HORAS_SIMULACION + 1)
    db.add(tenant)
    db.commit()
    tenant_id = tenant.id

    with patch("app.core.demo_simulador.httpx.post"):
        demo_simulador._tick_simulacion()

    db.expire_all()
    tenant_db = db.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
    assert tenant_db.demo_simulando_desde is None


def test_tick_no_autofrena_una_simulacion_reciente(db):
    tenant = demo_simulador.crear_estructura_demo(db, "Demo Reciente", "textil")
    tenant.demo_simulando_desde = datetime.utcnow() - timedelta(minutes=5)
    db.add(tenant)
    db.commit()

    with patch("app.core.demo_simulador.httpx.post") as mock_post:
        demo_simulador._tick_simulacion()

    db.refresh(tenant)
    assert tenant.demo_simulando_desde is not None  # sigue corriendo, no se autofrenó


def test_tick_intenta_enviar_un_scan_con_la_credencial_correcta(db):
    """Fuerza probabilidad=1 pisando MAX_HORAS y usando velocidad rapida
    no alcanza para determinismo (es probabilístico por diseño) -- en
    vez de eso se valida directo _enviar_scan, la pieza no-probabilística
    del tick."""
    tenant = demo_simulador.crear_estructura_demo(db, "Demo Envio", "textil")
    cred = db.exec(select(DemoCredencialSimulador).where(DemoCredencialSimulador.tenant_id == tenant.id)).first()

    with patch("app.core.demo_simulador.httpx.post") as mock_post:
        demo_simulador._enviar_scan(cred.credencial_completa, cred.estacion_id)

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["X-Device-Key"] == cred.credencial_completa
    assert kwargs["json"]["id_estacion"] == str(cred.estacion_id)
    assert kwargs["json"]["codigo_pieza"] is None
    assert kwargs["json"]["unidades_procesadas"] == 1


def test_limpieza_borra_demo_expirada_y_no_toca_una_vigente(db):
    expirada = demo_simulador.crear_estructura_demo(db, "Demo Vieja", "textil")
    expirada.demo_expira_at = datetime.utcnow() - timedelta(days=1)
    db.add(expirada)

    vigente = demo_simulador.crear_estructura_demo(db, "Demo Nueva", "textil")
    # demo_expira_at ya queda en el futuro por default (DIAS_RETENCION_DEMO)
    db.commit()
    # Capturados ANTES de expire_all() -- expirada.id después de eso
    # dispararía su propio refresh contra una fila ya borrada.
    id_expirada, id_vigente = expirada.id, vigente.id

    demo_simulador._limpiar_demos_expiradas()

    # _limpiar_demos_expiradas corre en su PROPIA Session(engine) --
    # borra "expirada" por otra conexión. La sesión de este test todavía
    # tiene esa fila en su identity map (quedó "expired" por el commit
    # de arriba, no removida) -- sin este expire_all, el SELECT de abajo
    # intentaría REFRESCAR el objeto ya trackeado, lo encontraría
    # borrado y tiraría ObjectDeletedError en vez de simplemente no
    # encontrar nada.
    db.expire_all()

    assert db.exec(select(Tenant).where(Tenant.id == id_expirada)).first() is None
    assert db.exec(select(Tenant).where(Tenant.id == id_vigente)).first() is not None

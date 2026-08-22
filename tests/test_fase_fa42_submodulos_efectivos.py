"""Fase FA.4.2 (PRD Segmentación de Planes §3.1): resolución de qué
submódulos tiene un tenant -- unión de los que traen sus planes activos
MÁS los contratados sueltos como add-on.

Nota de vocabulario: el PRD llama "submódulo" a lo que en el código es
`CaracteristicaModulo` (Fase FA.3) -- se decidió reusar esa entidad en
vez de crear `submodulos`/`plan_submodulos`, estructuralmente idénticas.

El gate es ESTRICTO, sin fallback: un tenant sin planes ni add-ons
devuelve conjunto vacío. Los tenants que ya operaban reciben su
asignación explícita desde seed.py, que es dato auditable en vez de una
excepción escondida en el código."""
import uuid
from datetime import date
from decimal import Decimal

from app.core.planes import limite_efectivo, submodulos_efectivos_tenant
from app.models.domain import (
    AsignacionModuloTenant, CaracteristicaModulo, EstadoAsignacionModulo,
    MetodoPagoConfigurado, ModuloDisponible, PlanCaracteristica, PlanPrecio,
    TenantAddonContratado,
)
from tests.conftest import autenticar_como


def _codigo(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:8]}"


def _crear_modulo_con_plan_y_submodulos(db, codigos_submodulos, **kwargs_plan):
    """Módulo + plan + N submódulos incluidos en ese plan."""
    modulo = ModuloDisponible(codigo=_codigo("mod"), nombre="Módulo Test")
    db.add(modulo)
    db.commit()
    db.refresh(modulo)

    plan = PlanPrecio(
        modulo_id=modulo.id, codigo="plan", nombre="Plan Test",
        precio=Decimal("100.00"), **kwargs_plan,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    caracteristicas = []
    for codigo in codigos_submodulos:
        c = CaracteristicaModulo(modulo_id=modulo.id, codigo=codigo, nombre=codigo)
        db.add(c)
        db.commit()
        db.refresh(c)
        db.add(PlanCaracteristica(plan_id=plan.id, caracteristica_id=c.id))
        caracteristicas.append(c)
    db.commit()
    return modulo, plan, caracteristicas


def _asignar_plan(db, tenant_id, modulo, plan, estado=EstadoAsignacionModulo.ACTIVA):
    metodo = MetodoPagoConfigurado(codigo=_codigo("mp"), nombre="Test", tipo="transferencia")
    db.add(metodo)
    db.commit()
    db.refresh(metodo)

    hoy = date.today()
    asignacion = AsignacionModuloTenant(
        tenant_id=tenant_id, modulo_id=modulo.id, plan_id=plan.id,
        metodo_pago_id=metodo.id, fecha_inicio=hoy,
        fecha_renovacion=hoy.replace(year=hoy.year + 1),
        precio_base=Decimal("100.00"), precio_con_descuento=Decimal("100.00"),
        estado=estado,
    )
    db.add(asignacion)
    db.commit()
    return asignacion


def test_tenant_sin_nada_no_tiene_submodulos(db, tenant_a):
    """Gate estricto: sin plan ni add-ons, conjunto vacío. NO se abre
    todo 'por las dudas'."""
    assert submodulos_efectivos_tenant(db, tenant_a) == set()


def test_submodulos_vienen_del_plan_activo(db, tenant_a):
    modulo, plan, _ = _crear_modulo_con_plan_y_submodulos(db, ["nucleo", "planificacion"])
    _asignar_plan(db, tenant_a, modulo, plan)

    assert submodulos_efectivos_tenant(db, tenant_a) == {"nucleo", "planificacion"}


def test_plan_suspendido_no_da_acceso(db, tenant_a):
    modulo, plan, _ = _crear_modulo_con_plan_y_submodulos(db, ["nucleo"])
    _asignar_plan(db, tenant_a, modulo, plan, estado=EstadoAsignacionModulo.SUSPENDIDA)

    assert submodulos_efectivos_tenant(db, tenant_a) == set()


def test_addon_suelto_da_acceso_sin_ningun_plan(db, tenant_a):
    """El caso central del PRD: un tenant Free contrata SOLO
    Planificación, sin subir de plan."""
    modulo = ModuloDisponible(codigo=_codigo("mod"), nombre="M")
    db.add(modulo)
    db.commit()
    db.refresh(modulo)
    caracteristica = CaracteristicaModulo(modulo_id=modulo.id, codigo="planificacion", nombre="Planificación")
    db.add(caracteristica)
    db.commit()
    db.refresh(caracteristica)

    db.add(TenantAddonContratado(
        tenant_id=tenant_a, caracteristica_id=caracteristica.id,
        precio=Decimal("50.00"), fecha_inicio=date.today(),
    ))
    db.commit()

    assert submodulos_efectivos_tenant(db, tenant_a) == {"planificacion"}


def test_union_de_plan_y_addon(db, tenant_a):
    """Los dos caminos son intercambiables: no importa por cuál llegó."""
    modulo, plan, _ = _crear_modulo_con_plan_y_submodulos(db, ["nucleo"])
    _asignar_plan(db, tenant_a, modulo, plan)

    extra = CaracteristicaModulo(modulo_id=modulo.id, codigo="personas_rrhh", nombre="Personas")
    db.add(extra)
    db.commit()
    db.refresh(extra)
    db.add(TenantAddonContratado(
        tenant_id=tenant_a, caracteristica_id=extra.id,
        precio=Decimal("10.00"), fecha_inicio=date.today(),
    ))
    db.commit()

    assert submodulos_efectivos_tenant(db, tenant_a) == {"nucleo", "personas_rrhh"}


def test_addon_cancelado_no_da_acceso(db, tenant_a):
    modulo = ModuloDisponible(codigo=_codigo("mod"), nombre="M")
    db.add(modulo)
    db.commit()
    db.refresh(modulo)
    caracteristica = CaracteristicaModulo(modulo_id=modulo.id, codigo="x", nombre="X")
    db.add(caracteristica)
    db.commit()
    db.refresh(caracteristica)
    db.add(TenantAddonContratado(
        tenant_id=tenant_a, caracteristica_id=caracteristica.id,
        precio=Decimal("10.00"), fecha_inicio=date.today(), estado="cancelada",
    ))
    db.commit()

    assert submodulos_efectivos_tenant(db, tenant_a) == set()


def test_no_se_mezclan_tenants(db, tenant_a, tenant_b):
    modulo, plan, _ = _crear_modulo_con_plan_y_submodulos(db, ["nucleo"])
    _asignar_plan(db, tenant_a, modulo, plan)

    assert submodulos_efectivos_tenant(db, tenant_a) == {"nucleo"}
    assert submodulos_efectivos_tenant(db, tenant_b) == set()


# ---------- Endpoint ----------

def test_endpoint_devuelve_los_codigos_ordenados(client, db, tenant_a, gerente_a):
    modulo, plan, _ = _crear_modulo_con_plan_y_submodulos(db, ["planificacion", "nucleo"])
    _asignar_plan(db, tenant_a, modulo, plan)

    autenticar_como(gerente_a.id)
    r = client.get("/billing/mi-empresa/submodulos-efectivos")
    assert r.status_code == 200, r.text
    assert r.json()["codigos"] == ["nucleo", "planificacion"]


def test_endpoint_accesible_para_cualquier_rol_del_tenant(client, db, tenant_a):
    """A diferencia del resto de /billing (SuperAdmin/Gerencia), esto lo
    necesita cualquier usuario para que su propio menú se arme bien."""
    from app.models.domain import RolUsuario
    from tests.conftest import crear_usuario

    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    autenticar_como(supervisor.id)
    r = client.get("/billing/mi-empresa/submodulos-efectivos")
    assert r.status_code == 200


# ---------- Add-ons (CRUD SuperAdmin) ----------

def test_contratar_addon_y_no_duplicarlo(client, db, tenant_a, superadmin):
    modulo = ModuloDisponible(codigo=_codigo("mod"), nombre="M")
    db.add(modulo)
    db.commit()
    db.refresh(modulo)
    caracteristica = CaracteristicaModulo(modulo_id=modulo.id, codigo="planificacion", nombre="Planificación")
    db.add(caracteristica)
    db.commit()
    db.refresh(caracteristica)

    autenticar_como(superadmin.id)
    payload = {
        "caracteristica_id": str(caracteristica.id),
        "precio": "50.00",
        "fecha_inicio": date.today().isoformat(),
    }
    r = client.post(f"/billing/clientes/{tenant_a}/addons", json=payload)
    assert r.status_code == 201, r.text
    addon_id = r.json()["id"]

    r = client.post(f"/billing/clientes/{tenant_a}/addons", json=payload)
    assert r.status_code == 409  # no se contrata dos veces

    r = client.get(f"/billing/clientes/{tenant_a}/addons")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.delete(f"/billing/clientes/{tenant_a}/addons/{addon_id}")
    assert r.status_code == 204
    assert submodulos_efectivos_tenant(db, tenant_a) == set()


def test_no_superadmin_no_puede_contratar_addon(client, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.post(f"/billing/clientes/{tenant_a}/addons", json={
        "caracteristica_id": str(uuid.uuid4()), "precio": "1.00", "fecha_inicio": date.today().isoformat(),
    })
    assert r.status_code == 403


# ---------- Los dos mecanismos son independientes (PRD §3.3) ----------

def test_addon_no_agrega_cupo_de_usuarios(db, tenant_a):
    """Caso de QA explícito del PRD: contratar un add-on habilita una
    PANTALLA, nunca cupo de usuarios. Son dos funciones distintas que no
    se consultan entre sí."""
    modulo, plan, _ = _crear_modulo_con_plan_y_submodulos(db, ["nucleo"], limite_usuarios=1)
    _asignar_plan(db, tenant_a, modulo, plan)

    extra = CaracteristicaModulo(modulo_id=modulo.id, codigo="planificacion", nombre="P")
    db.add(extra)
    db.commit()
    db.refresh(extra)
    db.add(TenantAddonContratado(
        tenant_id=tenant_a, caracteristica_id=extra.id,
        precio=Decimal("10.00"), fecha_inicio=date.today(),
    ))
    db.commit()

    # El add-on suma acceso...
    assert submodulos_efectivos_tenant(db, tenant_a) == {"nucleo", "planificacion"}
    # ...pero NO cupo.
    assert limite_efectivo(db, tenant_a, "limite_usuarios") == 1


def test_limite_none_en_algun_plan_es_ilimitado(db, tenant_a):
    """Si CUALQUIER plan activo tiene el eje en None, el conjunto es
    ilimitado -- no se puede sumar un ilimitado a un número."""
    modulo, plan, _ = _crear_modulo_con_plan_y_submodulos(db, ["nucleo"], limite_usuarios=None)
    _asignar_plan(db, tenant_a, modulo, plan)

    assert limite_efectivo(db, tenant_a, "limite_usuarios") is None


def test_limite_sin_ningun_plan_es_none(db, tenant_a):
    """Sin plan no hay quien imponga un límite; inventarle uno no es rol
    de esta función."""
    assert limite_efectivo(db, tenant_a, "limite_usuarios") is None

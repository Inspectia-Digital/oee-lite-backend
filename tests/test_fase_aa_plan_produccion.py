"""Fase AA (pedido de Green Mills, reunión de producción): Plan de
Producción -- nuevo nivel arriba de Orden, agrupa lo que se va a
producir en un día. El supervisor avanza de una orden a la siguiente
(POST /supervisor/planes/{id}/avanzar-orden/); mientras el plan tiene
una orden activa, esa es la fuente autoritativa de "qué SKU está
corriendo ahora" tanto para la ingesta real (/api/lite/scans) como para
la portada (/analytics/linea-en-vivo/) -- una sola fuente de verdad para
las dos, ver resolver_orden_activa en clasificacion.py.
"""
import io
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import (
    Estacion, EstadoOrden, Linea, MaestroSKU, Planta, RolUsuario, Tenant,
)
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AA")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea AA")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación AA", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return planta, linea, estacion


def _crear_sku(db, tenant_id, tiempo_ideal_seg=10.0):
    # Fase AC: perfil completo (no sólo ideal) -- si faltara lento/alerta
    # el SKU cae al piso de Línea, y estos tests verifican justamente que
    # se usa EL IDEAL DE ESTE SKU puntual (ver evento.tiempo_ideal_seg).
    sku = MaestroSKU(
        tenant_id=tenant_id, codigo_sku=f"SKU-{uuid.uuid4().hex[:8]}",
        descripcion="SKU de prueba", tiempo_ideal_seg=tiempo_ideal_seg,
        tiempo_lento_seg=tiempo_ideal_seg + 5, tiempo_alerta_seg=tiempo_ideal_seg + 10,
        unidades_por_ciclo=1,
    )
    db.add(sku)
    db.commit()
    return sku


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


# ---------- CRUD de Plan ----------

def test_crear_plan_vacio(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan vacío"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["estado"] == "abierto"
    assert body["orden_activa_fk"] is None
    assert body["ordenes"] == []


def test_crear_plan_linea_de_otro_tenant_devuelve_400(client, db, tenant_a, tenant_b, gerente_a):
    planta_b, linea_b, _ = _preparar_escenario(db, tenant_b)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/planes/",
        json={"linea_id": str(linea_b.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan cross-tenant"},
    )
    assert r.status_code == 400


def test_crear_plan_requiere_nombre(client, db, tenant_a, gerente_a):
    """Unificación UX Planes/Órdenes/SKUs: antes un plan sólo se
    identificaba por (línea, fecha) -- ahora puede haber varios el mismo
    día y el nombre es obligatorio para poder distinguirlos."""
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat()},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 422

    r2 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "   "},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r2.status_code == 422


def test_crear_plan_nombre_se_persiste_y_se_puede_renombrar(client, db, tenant_a, gerente_a):
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    r = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Turno mañana"},
        headers=headers,
    )
    assert r.status_code == 201
    plan = r.json()
    assert plan["nombre"] == "Turno mañana"

    r2 = client.patch(f"/config/planes/{plan['id']}", json={"nombre": "Turno mañana (revisado)"}, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["nombre"] == "Turno mañana (revisado)"

    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    assert detalle["nombre"] == "Turno mañana (revisado)"


def test_crear_plan_bloqueado_si_origen_erp(client, db, tenant_a, gerente_a):
    tenant_db = db.exec(select(Tenant).where(Tenant.id == tenant_a)).first()
    tenant_db.origen_maestros = "ERP"
    db.add(tenant_db)
    db.commit()

    planta, linea, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan bloqueado"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 409


def test_crear_orden_bloqueada_si_origen_erp(client, db, tenant_a, gerente_a):
    tenant_db = db.exec(select(Tenant).where(Tenant.id == tenant_a)).first()
    tenant_db.origen_maestros = "ERP"
    db.add(tenant_db)
    db.commit()

    planta, linea, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-ERP-{uuid.uuid4().hex[:6]}", "linea_id": str(linea.id), "cantidad_esperada": 10},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 409


def test_subir_plan_con_plan_id_asocia_ordenes_y_autoasigna_secuencia(client, db, tenant_a, gerente_a):
    """El upload masivo (/plan/upload) tampoco seteaba plan_id -- las
    órdenes cargadas por Excel quedaban invisibles para "Plan del día"
    (Supervisor) aunque la línea tuviera un Plan abierto."""
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    sku1 = _crear_sku(db, tenant_a)
    sku2 = _crear_sku(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan por Excel"},
        headers=headers,
    ).json()

    id_orden_1 = f"OP-UP-{uuid.uuid4().hex[:6]}"
    id_orden_2 = f"OP-UP-{uuid.uuid4().hex[:6]}"
    csv_content = (
        "id_orden,sku_fk,cantidad_esperada,plan_fecha\n"
        f"{id_orden_1},{sku1.codigo_sku},100,{date.today().isoformat()}\n"
        f"{id_orden_2},{sku2.codigo_sku},50,{date.today().isoformat()}\n"
    )

    r = client.post(
        "/api/lite/importaciones/plan/upload",
        data={"linea_id": str(linea.id), "plan_id": plan["id"]},
        files={"file": ("plan.csv", csv_content.encode(), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["resultados"]["creadas"] == 2

    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    ordenes_por_id = {o["id_orden"]: o for o in detalle["ordenes"]}
    assert ordenes_por_id[id_orden_1]["secuencia"] == 1
    assert ordenes_por_id[id_orden_2]["secuencia"] == 2


def test_subir_plan_con_plan_id_de_otra_linea_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    linea_b = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea AA otra")
    db.add(linea_b)
    db.commit()
    db.refresh(linea_b)
    sku = _crear_sku(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan línea A"},
        headers=headers,
    ).json()

    csv_content = f"id_orden,sku_fk,cantidad_esperada,plan_fecha\nOP-X,{sku.codigo_sku},10,{date.today().isoformat()}\n"
    r = client.post(
        "/api/lite/importaciones/plan/upload",
        # linea_id apunta a linea_b, pero el plan es de linea -- deben coincidir.
        data={"linea_id": str(linea_b.id), "plan_id": plan["id"]},
        files={"file": ("plan.csv", csv_content.encode(), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 400


def test_crear_orden_con_plan_id_autoasigna_secuencia(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    sku1 = _crear_sku(db, tenant_a)
    sku2 = _crear_sku(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan autoasigna"},
        headers=headers,
    ).json()

    o1 = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-{uuid.uuid4().hex[:6]}", "sku_fk": sku1.codigo_sku, "cantidad_esperada": 100, "plan_id": plan["id"]},
        headers=headers,
    ).json()
    o2 = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-{uuid.uuid4().hex[:6]}", "sku_fk": sku2.codigo_sku, "cantidad_esperada": 50, "plan_id": plan["id"]},
        headers=headers,
    ).json()
    assert o1["secuencia"] == 1
    assert o2["secuencia"] == 2

    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    assert [o["id_orden"] for o in detalle["ordenes"]] == [o1["id_orden"], o2["id_orden"]]
    assert all(o["cantidad_producida"] == 0 for o in detalle["ordenes"])


def test_listar_planes_filtra_por_linea(client, db, tenant_a, gerente_a):
    planta, linea1, _ = _preparar_escenario(db, tenant_a)
    linea2 = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea AA2")
    db.add(linea2)
    db.commit()
    db.refresh(linea2)

    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    client.post(
        "/config/planes/",
        json={"linea_id": str(linea1.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan línea 1"},
        headers=headers,
    )
    client.post(
        "/config/planes/",
        json={"linea_id": str(linea2.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan línea 2"},
        headers=headers,
    )

    r = client.get("/config/planes/", params={"linea_id": str(linea1.id)}, headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1


# ---------- avanzar_orden ----------

def _crear_plan_con_dos_ordenes(client, db, tenant_id, planta, linea, headers):
    sku1 = _crear_sku(db, tenant_id, tiempo_ideal_seg=10.0)
    sku2 = _crear_sku(db, tenant_id, tiempo_ideal_seg=20.0)
    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan avanzar_orden"},
        headers=headers,
    ).json()
    o1 = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-A-{uuid.uuid4().hex[:6]}", "sku_fk": sku1.codigo_sku, "cantidad_esperada": 100, "plan_id": plan["id"]},
        headers=headers,
    ).json()
    o2 = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-B-{uuid.uuid4().hex[:6]}", "sku_fk": sku2.codigo_sku, "cantidad_esperada": 50, "plan_id": plan["id"]},
        headers=headers,
    ).json()
    return plan, o1, o2, sku1, sku2


def test_avanzar_orden_primera_vez_activa_la_primera_de_la_secuencia(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)

    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["orden_cerrada_id_orden"] is None
    assert body["orden_activa_id_orden"] == o1["id_orden"]
    assert body["estado"] == "abierto"


def test_avanzar_orden_segunda_vez_cierra_la_primera_y_activa_la_segunda(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)

    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["orden_cerrada_id_orden"] == o1["id_orden"]
    assert body["orden_activa_id_orden"] == o2["id_orden"]
    assert body["estado"] == "abierto"

    o1_db = client.get(f"/config/ordenes/{o1['id_orden']}", headers=headers).json()
    assert o1_db["estado"] == "cerrada"


def test_avanzar_orden_sin_mas_ordenes_cierra_el_plan_solo(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)

    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # activa o1
    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # cierra o1, activa o2
    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # cierra o2, no queda nada
    assert r.status_code == 200
    body = r.json()
    assert body["orden_cerrada_id_orden"] == o2["id_orden"]
    assert body["orden_activa_id_orden"] is None
    assert body["estado"] == "cerrado"


def test_avanzar_orden_plan_ya_cerrado_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    for _ in range(3):
        client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)

    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    assert r.status_code == 409


def test_avanzar_orden_plan_de_otro_tenant_devuelve_404(client, db, tenant_a, tenant_b, gerente_a):
    planta_a, _, _ = _preparar_escenario(db, tenant_a)
    planta_b, linea_b, _ = _preparar_escenario(db, tenant_b)
    from app.models.domain import PlanProduccion
    plan_b = PlanProduccion(tenant_id=tenant_b, linea_id=linea_b.id, fecha_inicio=date.today())
    db.add(plan_b)
    db.commit()
    db.refresh(plan_b)

    autenticar_como(gerente_a.id)
    # validar_planta exige X-Sub-Tenant-Id de ALGUNA planta propia incluso
    # para Gerencia (el bypass es sólo de la membresía UsuarioPlanta, no
    # de la presencia del header) -- se manda la propia planta de
    # tenant_a; el aislamiento de tenant lo da el filtro tenant_id de la
    # query, no de qué planta se mande acá.
    r = client.post(f"/supervisor/planes/{plan_b.id}/avanzar-orden/", headers={"X-Sub-Tenant-Id": str(planta_a.id)})
    assert r.status_code == 404


def test_avanzar_orden_operario_no_puede(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)

    operario_usuario = crear_usuario(db, tenant_a, RolUsuario.OPERARIO)
    from app.models.domain import UsuarioPlanta
    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=operario_usuario.id, planta_id=planta.id, activo=True))
    db.commit()

    autenticar_como(operario_usuario.id)
    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    assert r.status_code == 403


def test_avanzar_orden_supervisor_puede(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)

    supervisor_usuario = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    from app.models.domain import UsuarioPlanta
    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=supervisor_usuario.id, planta_id=planta.id, activo=True))
    db.commit()

    autenticar_como(supervisor_usuario.id)
    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    assert r.status_code == 200


# ---------- Integración con ingesta real (/api/lite/scans) ----------

def test_scans_usa_la_orden_activa_del_plan_no_el_heuristico_en_progreso(client, db, tenant_a, gerente_a):
    """El núcleo del pedido: mientras el plan tiene una orden activa, ESA
    es la que scans.py usa para resolver SKU/tiempo ideal -- no "la
    EN_PROGRESO más reciente" (que en este test es deliberadamente OTRA
    orden, ajena al plan, para probar que el plan gana)."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, sku1, sku2 = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)

    # Orden EN_PROGRESO ajena al plan -- si el heurístico viejo ganara,
    # el evento resolvería el SKU de ÉSTA, no el del plan.
    from app.models.domain import OrdenProduccion
    orden_ajena = OrdenProduccion(
        tenant_id=tenant_a, id_orden=f"OP-AJENA-{uuid.uuid4().hex[:6]}", linea_id=linea.id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=_crear_sku(db, tenant_a, tiempo_ideal_seg=999.0).codigo_sku,
    )
    db.add(orden_ajena)
    db.commit()

    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # activa o1 (sku1, ideal=10s)

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201

    from app.models.domain import LiteEventoProduccion
    from sqlmodel import select
    evento = db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id))).first()
    assert evento.orden_fk == o1["id_orden"]
    assert evento.tiempo_ideal_seg == 10.0  # el ideal de sku1 (plan), no el de orden_ajena (999s)


def test_scans_sigue_la_orden_activa_a_traves_de_un_avance(client, db, tenant_a, gerente_a):
    """Tras avanzar_orden, el próximo scan ya clasifica contra la
    SIGUIENTE orden -- no queda pegado a la que se acaba de cerrar."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, sku1, sku2 = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # activa o1
    r1 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r1.status_code == 201

    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # cierra o1, activa o2
    r2 = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r2.status_code == 201

    from app.models.domain import LiteEventoProduccion
    from sqlmodel import select
    eventos = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id)).order_by(LiteEventoProduccion.timestamp)
    ).all()
    assert eventos[0].orden_fk == o1["id_orden"]
    assert eventos[1].orden_fk == o2["id_orden"]
    assert eventos[1].tiempo_ideal_seg == 20.0  # el ideal de sku2


def test_linea_sin_plan_sigue_con_el_heuristico_de_siempre(client, db, tenant_a, gerente_a):
    """Retrocompatibilidad explícita: una línea que nunca crea un Plan no
    ve NINGÚN cambio de comportamiento -- Plan es opcional."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    sku = _crear_sku(db, tenant_a, tiempo_ideal_seg=15.0)
    from app.models.domain import OrdenProduccion
    orden = OrdenProduccion(
        tenant_id=tenant_a, id_orden=f"OP-SOLA-{uuid.uuid4().hex[:6]}", linea_id=linea.id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=sku.codigo_sku,
    )
    db.add(orden)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201

    from app.models.domain import LiteEventoProduccion
    from sqlmodel import select
    evento = db.exec(select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id))).first()
    assert evento.orden_fk == orden.id_orden
    assert evento.tiempo_ideal_seg == 15.0


def test_linea_en_vivo_refleja_la_orden_activa_del_plan(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, sku1, sku2 = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # activa o1

    r = client.get("/analytics/linea-en-vivo/", params={"linea_id": str(linea.id)}, headers=headers)
    assert r.status_code == 200
    assert r.json()["orden_activa"] == o1["id_orden"]
    assert r.json()["orden_sku"] == sku1.codigo_sku

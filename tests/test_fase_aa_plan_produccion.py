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
    # QA-01: nace en_progreso directo -- no había otro plan en_progreso
    # en la línea, mismo comportamiento de siempre (antes "abierto").
    assert body["estado"] == "en_progreso"
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


def test_plan_expone_campo_activo_y_se_desactiva_con_baja_logica(client, db, tenant_a, gerente_a):
    """PlanConOrdenes -- la respuesta real de POST/GET/PATCH
    /config/planes/{id} -- no serializaba `activo` aunque el ORM siempre
    lo tuvo: plan.activo daba undefined en el front y el botón "Cerrar
    plan" de PlanesPanel.tsx no se mostraba nunca, ni para un plan
    abierto. Cubre el ida y vuelta completo: el alta expone activo=true
    (precondición para que el botón aparezca) y la baja lógica (DELETE)
    lo deja en false en el detalle."""
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan activo"},
        headers=headers,
    ).json()
    assert plan["activo"] is True

    r = client.delete(f"/config/planes/{plan['id']}", headers=headers)
    assert r.status_code == 200

    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    assert detalle["activo"] is False


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
    assert body["estado"] == "en_progreso"


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
    assert body["estado"] == "en_progreso"

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


# ---------- Fase AH (auditoría QA, QA-01/QA-13): máquina de estados ----------

def test_crear_segundo_plan_en_misma_linea_nace_programado(client, db, tenant_a, gerente_a):
    """QA-01: si la línea ya tiene un plan EN_PROGRESO, el nuevo no
    compite -- nace PROGRAMADO, no EN_PROGRESO."""
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    p1 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 1"},
        headers=headers,
    ).json()
    assert p1["estado"] == "en_progreso"

    p2 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 2 (urgente)"},
        headers=headers,
    ).json()
    assert p2["estado"] == "programado"


def test_activar_plan_programado_sin_conflicto(client, db, tenant_a, gerente_a):
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    p1 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 1"},
        headers=headers,
    ).json()
    p2 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 2"},
        headers=headers,
    ).json()
    assert p2["estado"] == "programado"

    # Cerrando/cancelando el primero, activar el segundo debe funcionar.
    client.delete(f"/config/planes/{p1['id']}", headers=headers)
    r = client.post(f"/config/planes/{p2['id']}/activar", headers=headers)
    assert r.status_code == 200
    assert r.json()["estado"] == "en_progreso"


def test_activar_plan_con_otro_en_progreso_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 1"},
        headers=headers,
    )
    p2 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 2"},
        headers=headers,
    ).json()
    assert p2["estado"] == "programado"

    r = client.post(f"/config/planes/{p2['id']}/activar", headers=headers)
    assert r.status_code == 409


def test_activar_plan_que_no_esta_en_borrador_ni_programado_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    p1 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 1"},
        headers=headers,
    ).json()
    assert p1["estado"] == "en_progreso"

    r = client.post(f"/config/planes/{p1['id']}/activar", headers=headers)
    assert r.status_code == 409


def test_desactivar_plan_en_progreso_lo_cancela_y_cierra_orden_activa(client, db, tenant_a, gerente_a):
    """QA-13: antes desactivar_plan sólo ponía activo=False, sin tocar
    estado ni orden_activa_fk -- resolver_orden_activa caía al
    heurístico de EN_PROGRESO más reciente y la orden "cancelada" seguía
    recibiendo scans. Ahora cancela de verdad."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # activa o1

    r = client.delete(f"/config/planes/{plan['id']}", headers=headers)
    assert r.status_code == 200

    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    assert detalle["estado"] == "cancelado"
    assert detalle["activo"] is False
    assert detalle["orden_activa_fk"] is None

    o1_db = client.get(f"/config/ordenes/{o1['id_orden']}", headers=headers).json()
    assert o1_db["estado"] == "cerrada"


def test_desactivar_plan_cerrado_no_cambia_estado(client, db, tenant_a, gerente_a):
    """Un plan que ya terminó su secuencia normalmente (CERRADO) no se
    reescribe a CANCELADO al hacer la baja lógica -- ya terminó bien."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # agota la secuencia
    assert r.json()["estado"] == "cerrado"

    client.delete(f"/config/planes/{plan['id']}", headers=headers)
    detalle = client.get(f"/config/planes/{plan['id']}", headers=headers).json()
    assert detalle["estado"] == "cerrado"
    assert detalle["activo"] is False


def test_avanzar_orden_bloqueado_si_plan_todavia_no_esta_en_progreso(client, db, tenant_a, gerente_a):
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 1"},
        headers=headers,
    )
    p2 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 2"},
        headers=headers,
    ).json()
    assert p2["estado"] == "programado"

    r = client.post(f"/supervisor/planes/{p2['id']}/avanzar-orden/", headers=headers)
    assert r.status_code == 409


def test_avanzar_orden_bloqueado_si_plan_cancelado(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    client.delete(f"/config/planes/{plan['id']}", headers=headers)  # cancela

    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    assert r.status_code == 409


def test_resolver_orden_activa_ignora_plan_programado(client, db, tenant_a, gerente_a):
    """Defensa en profundidad: aunque en la práctica un plan PROGRAMADO
    nunca debería tener orden_activa_fk (avanzar_orden ya lo bloquea),
    resolver_orden_activa (vía linea-en-vivo) sólo debe mirar
    EN_PROGRESO -- nunca un plan en cola."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan1, o1, o2, sku1, sku2 = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    client.post(f"/supervisor/planes/{plan1['id']}/avanzar-orden/", headers=headers)  # activa o1 en plan1 (en_progreso)

    plan2 = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan 2 (cola)"},
        headers=headers,
    ).json()
    assert plan2["estado"] == "programado"

    r = client.get("/analytics/linea-en-vivo/", params={"linea_id": str(linea.id)}, headers=headers)
    assert r.status_code == 200
    # Sigue viendo la orden del plan EN_PROGRESO, no se confunde con el
    # plan en cola.
    assert r.json()["orden_activa"] == o1["id_orden"]


# ---------- Fase AI (auditoría QA, QA-02): línea Orden == línea Plan ----------

def test_crear_orden_con_linea_distinta_a_la_del_plan_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea_a, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    linea_b = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea B")
    db.add(linea_b)
    db.commit()
    db.refresh(linea_b)

    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea_a.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan en línea A"},
        headers=headers,
    ).json()

    r = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-{uuid.uuid4().hex[:6]}", "cantidad_esperada": 100, "plan_id": plan["id"], "linea_id": str(linea_b.id)},
        headers=headers,
    )
    assert r.status_code == 400


def test_crear_orden_en_plan_hereda_la_linea_si_no_se_manda(client, db, tenant_a, gerente_a):
    """Mismo criterio que useCrearOrdenEnPlan en el front: nunca manda
    linea_id -- el backend la resuelve del plan."""
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan"},
        headers=headers,
    ).json()

    r = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-{uuid.uuid4().hex[:6]}", "cantidad_esperada": 100, "plan_id": plan["id"]},
        headers=headers,
    )
    assert r.status_code == 201
    assert r.json()["linea_id"] == str(linea.id)


def test_crear_orden_en_plan_cerrado_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)
    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # agota la secuencia, cierra el plan

    r = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-{uuid.uuid4().hex[:6]}", "cantidad_esperada": 100, "plan_id": plan["id"]},
        headers=headers,
    )
    assert r.status_code == 409


def test_actualizar_orden_con_linea_distinta_a_la_de_su_plan_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea_a, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    linea_b = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea B (update)")
    db.add(linea_b)
    db.commit()
    db.refresh(linea_b)

    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea_a.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan"},
        headers=headers,
    ).json()
    orden = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-{uuid.uuid4().hex[:6]}", "cantidad_esperada": 100, "plan_id": plan["id"]},
        headers=headers,
    ).json()

    r = client.patch(
        f"/config/ordenes/{orden['id_orden']}",
        json={"linea_id": str(linea_b.id)},
        headers=headers,
    )
    assert r.status_code == 400


def test_actualizar_orden_sin_tocar_plan_ni_linea_no_revalida(client, db, tenant_a, gerente_a):
    """Editar un campo no relacionado (cantidad_esperada) nunca dispara
    la validación cruzada plan<->línea -- no debe bloquear ediciones de
    órdenes viejas por datos que pudieran haber quedado inconsistentes
    antes de este fix."""
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan"},
        headers=headers,
    ).json()
    orden = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-{uuid.uuid4().hex[:6]}", "cantidad_esperada": 100, "plan_id": plan["id"]},
        headers=headers,
    ).json()

    r = client.patch(f"/config/ordenes/{orden['id_orden']}", json={"cantidad_esperada": 250}, headers=headers)
    assert r.status_code == 200
    assert r.json()["cantidad_esperada"] == 250


# ---------- Fase AP (auditoría QA, QA-12): no avanzar a una orden inactiva ----------

def test_avanzar_orden_salta_la_siguiente_si_esta_inactiva(client, db, tenant_a, gerente_a):
    """QA-12: si la orden que seguía en la secuencia se desactivó desde
    Configuración (baja lógica) entre medio, avanzar_orden no debe
    reactivarla -- el plan se cierra solo, como si no quedara ninguna."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)

    # Desactiva o2 (la que seguiría en la secuencia) ANTES de avanzar.
    r = client.delete(f"/config/ordenes/{o2['id_orden']}", headers=headers)
    assert r.status_code == 200

    client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # activa o1
    r = client.post(f"/supervisor/planes/{plan['id']}/avanzar-orden/", headers=headers)  # o2 está inactiva -> se salta
    assert r.status_code == 200
    body = r.json()
    assert body["orden_cerrada_id_orden"] == o1["id_orden"]
    assert body["orden_activa_id_orden"] is None
    assert body["estado"] == "cerrado"

    o2_db = client.get(f"/config/ordenes/{o2['id_orden']}", headers=headers).json()
    assert o2_db["estado"] != "en_progreso"  # nunca se activó


# ---------- Fase AR (auditoría QA, QA-11): secuencia positiva y única ----------

def test_crear_orden_con_secuencia_negativa_en_plan_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan"},
        headers=headers,
    ).json()

    r = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-{uuid.uuid4().hex[:6]}", "cantidad_esperada": 100, "plan_id": plan["id"], "secuencia": -1},
        headers=headers,
    )
    assert r.status_code == 400


def test_crear_orden_con_secuencia_duplicada_en_plan_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, _ = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan = client.post(
        "/config/planes/",
        json={"linea_id": str(linea.id), "fecha_inicio": date.today().isoformat(), "nombre": "Plan"},
        headers=headers,
    ).json()
    client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-A-{uuid.uuid4().hex[:6]}", "cantidad_esperada": 100, "plan_id": plan["id"], "secuencia": 1},
        headers=headers,
    )

    r = client.post(
        "/config/ordenes/",
        json={"id_orden": f"OP-B-{uuid.uuid4().hex[:6]}", "cantidad_esperada": 50, "plan_id": plan["id"], "secuencia": 1},
        headers=headers,
    )
    assert r.status_code == 409


def test_actualizar_orden_a_secuencia_duplicada_en_plan_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)
    # o1 y o2 se autoasignaron secuencia 1 y 2 -- intentar mover o2 a la
    # misma secuencia que o1 debe fallar.
    r = client.patch(f"/config/ordenes/{o2['id_orden']}", json={"secuencia": 1}, headers=headers)
    assert r.status_code == 409


def test_actualizar_orden_a_su_propia_secuencia_no_falla(client, db, tenant_a, gerente_a):
    """Regresión: re-enviar la MISMA secuencia que ya tenía la orden no
    debe rechazarse como "duplicada consigo misma"."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)
    plan, o1, o2, *_ = _crear_plan_con_dos_ordenes(client, db, tenant_a, planta, linea, headers)

    r = client.patch(f"/config/ordenes/{o1['id_orden']}", json={"secuencia": 1}, headers=headers)
    assert r.status_code == 200

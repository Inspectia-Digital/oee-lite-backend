"""Fase BU (auditoría de robustez, batch 3): paginación opcional en
endpoints de catálogo. `limit`/`offset` son opcionales -- sin `limit`, el
endpoint se comporta EXACTAMENTE igual que antes (lista completa), para
no romper a los consumidores del frontend que hoy dependen de eso
(selects/dropdowns). El helper compartido es app/core/pagination.py;
estos tests cubren su comportamiento a fondo en dos endpoints
representativos (uno de configuracion.py, uno de personas.py) y
confirman con un smoke test que el resto de los endpoints migrados
acepta el parámetro correctamente.
"""
import uuid

from app.models.domain import MaestroSKU, Operario, OrdenProduccion, Planta, PlanProduccion, Linea, Supervisor
from tests.conftest import autenticar_como


def _crear_skus(db, tenant_id, n):
    codigos = []
    for i in range(n):
        codigo = f"SKU-PAG-{i:03d}-{uuid.uuid4().hex[:6]}"
        db.add(MaestroSKU(tenant_id=tenant_id, codigo_sku=codigo, descripcion=f"SKU paginación {i}"))
        codigos.append(codigo)
    db.commit()
    return sorted(codigos)


def _crear_operarios(db, tenant_id, n):
    legajos = []
    for i in range(n):
        legajo = f"LEG-PAG-{i:03d}-{uuid.uuid4().hex[:6]}"
        db.add(Operario(tenant_id=tenant_id, legajo=legajo, nombre_completo=f"Operario {i}"))
        legajos.append(legajo)
    db.commit()
    return sorted(legajos)


# ---------- listar_skus (representa configuracion.py) ----------

def test_listar_skus_sin_limit_devuelve_todo_igual_que_antes(client, db, tenant_a, gerente_a):
    codigos = _crear_skus(db, tenant_a, 7)
    autenticar_como(gerente_a.id)
    r = client.get("/config/erp/skus")
    assert r.status_code == 200
    devueltos = {s["codigo_sku"] for s in r.json()}
    assert set(codigos).issubset(devueltos)


def test_listar_skus_con_limit_trunca(client, db, tenant_a, gerente_a):
    _crear_skus(db, tenant_a, 5)
    autenticar_como(gerente_a.id)
    r = client.get("/config/erp/skus", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_listar_skus_offset_avanza_la_pagina(client, db, tenant_a, gerente_a):
    codigos = _crear_skus(db, tenant_a, 5)
    autenticar_como(gerente_a.id)
    pagina1 = client.get("/config/erp/skus", params={"limit": 2, "offset": 0}).json()
    pagina2 = client.get("/config/erp/skus", params={"limit": 2, "offset": 2}).json()
    ids_pagina1 = {s["codigo_sku"] for s in pagina1}
    ids_pagina2 = {s["codigo_sku"] for s in pagina2}
    assert ids_pagina1.isdisjoint(ids_pagina2)
    # Orden determinístico (order_by codigo_sku) -- misma consulta repetida
    # dos veces con el mismo offset da la misma página.
    assert client.get("/config/erp/skus", params={"limit": 2, "offset": 0}).json() == pagina1


def test_listar_skus_limit_invalido_devuelve_400(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    assert client.get("/config/erp/skus", params={"limit": 0}).status_code == 400
    assert client.get("/config/erp/skus", params={"limit": 501}).status_code == 400


def test_listar_skus_offset_negativo_devuelve_400(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/config/erp/skus", params={"offset": -1})
    assert r.status_code == 400


# ---------- listar_operarios (representa personas.py) ----------

def test_listar_operarios_sin_limit_devuelve_todo(client, db, tenant_a, gerente_a):
    legajos = _crear_operarios(db, tenant_a, 4)
    autenticar_como(gerente_a.id)
    r = client.get("/config/operarios/")
    assert r.status_code == 200
    assert set(legajos).issubset({o["legajo"] for o in r.json()})


def test_listar_operarios_con_limit_trunca(client, db, tenant_a, gerente_a):
    _crear_operarios(db, tenant_a, 4)
    autenticar_como(gerente_a.id)
    r = client.get("/config/operarios/", params={"limit": 1})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_listar_operarios_limit_invalido_devuelve_400(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    assert client.get("/config/operarios/", params={"limit": 0}).status_code == 400


# ---------- smoke test del resto de endpoints migrados ----------

def test_listar_ordenes_acepta_limit(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta Paginación")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea Paginación")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    for i in range(3):
        db.add(OrdenProduccion(tenant_id=tenant_a, id_orden=f"OP-PAG-{i}-{uuid.uuid4().hex[:6]}", linea_id=linea.id, cantidad_esperada=1))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get("/config/ordenes/", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_listar_planes_acepta_limit(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta Paginación Planes")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea Paginación Planes")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    for i in range(3):
        db.add(PlanProduccion(tenant_id=tenant_a, linea_id=linea.id, nombre=f"Plan {i}", fecha_inicio="2026-01-01"))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get("/config/planes/", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_listar_supervisores_acepta_limit(client, db, tenant_a, gerente_a):
    for i in range(3):
        db.add(Supervisor(tenant_id=tenant_a, legajo=f"SUP-PAG-{i}-{uuid.uuid4().hex[:6]}", nombre_completo=f"Supervisor {i}"))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get("/config/supervisores/", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()) == 2

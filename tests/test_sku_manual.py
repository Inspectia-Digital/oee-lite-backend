"""Fase M: alta manual de un SKU individual (POST /config/erp/skus).

Antes el alta de SKUs era exclusivamente vía importación de archivo
(/erp/skus/bulk o /api/lite/importaciones/skus/upload); no existía ningún
endpoint para crear un solo SKU, a diferencia de /config/ordenes/ que sí
lo tenía. Se agrega para habilitar la carga manual sin archivo desde el
front, igual que ya existe para el plan de producción.
"""
import uuid

from sqlmodel import select

from app.models.domain import Linea, MaestroSKU, Planta
from tests.conftest import autenticar_como


def test_crear_sku_manual(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    codigo = f"SKU-MANUAL-{uuid.uuid4().hex[:8]}"

    r = client.post(
        "/config/erp/skus",
        json={"codigo_sku": codigo, "descripcion": "Producto de prueba"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["codigo_sku"] == codigo
    assert body["descripcion"] == "Producto de prueba"
    # Defaults del modelo cuando no se envían.
    assert body["tiempo_ideal_seg"] == 240.0
    assert body["unidades_por_ciclo"] == 1

    sku_db = db.exec(
        select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo, MaestroSKU.tenant_id == tenant_a)
    ).first()
    assert sku_db is not None


def test_crear_sku_manual_con_linea(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta SKU Manual")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea SKU Manual")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    autenticar_como(gerente_a.id)
    codigo = f"SKU-MANUAL-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/config/erp/skus",
        json={
            "codigo_sku": codigo,
            "descripcion": "Con línea",
            "tiempo_ideal_seg": 120,
            "unidades_por_ciclo": 4,
            "linea_id": str(linea.id),
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["linea_id"] == str(linea.id)
    assert body["tiempo_ideal_seg"] == 120
    assert body["unidades_por_ciclo"] == 4


def test_crear_sku_manual_codigo_duplicado_devuelve_409(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    codigo = f"SKU-MANUAL-{uuid.uuid4().hex[:8]}"

    r1 = client.post("/config/erp/skus", json={"codigo_sku": codigo, "descripcion": "Primero"})
    assert r1.status_code == 201

    r2 = client.post("/config/erp/skus", json={"codigo_sku": codigo, "descripcion": "Segundo"})
    assert r2.status_code == 409


def test_crear_sku_manual_linea_de_otro_tenant_devuelve_400(client, db, tenant_a, gerente_a, tenant_b):
    planta_b = Planta(tenant_id=tenant_b, nombre="Planta B")
    db.add(planta_b)
    db.commit()
    db.refresh(planta_b)
    linea_b = Linea(tenant_id=tenant_b, planta_id=planta_b.id, nombre="Línea B")
    db.add(linea_b)
    db.commit()
    db.refresh(linea_b)

    autenticar_como(gerente_a.id)
    codigo = f"SKU-MANUAL-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/config/erp/skus",
        json={"codigo_sku": codigo, "descripcion": "Cross tenant", "linea_id": str(linea_b.id)},
    )
    assert r.status_code == 400


def test_crear_sku_manual_requiere_gerencia(client, db, tenant_a):
    from app.models.domain import RolUsuario
    from tests.conftest import crear_usuario

    operario = crear_usuario(db, tenant_a, RolUsuario.OPERARIO)
    autenticar_como(operario.id)

    r = client.post(
        "/config/erp/skus",
        json={"codigo_sku": f"SKU-{uuid.uuid4().hex[:8]}", "descripcion": "No autorizado"},
    )
    assert r.status_code == 403


def test_desactivar_sku_por_patch_persiste(client, db, tenant_a, gerente_a):
    """Bug real: activo/descripcion eran parámetros sueltos en la firma del
    endpoint (no un BaseModel) -- FastAPI los trataba como query params, el
    body JSON que manda el front (apiPatch) nunca llegaba. El PATCH
    devolvía 200 pero no cambiaba nada: activar/desactivar un SKU no hacía
    nada, en silencio."""
    autenticar_como(gerente_a.id)
    codigo = f"SKU-MANUAL-{uuid.uuid4().hex[:8]}"
    r = client.post("/config/erp/skus", json={"codigo_sku": codigo, "descripcion": "Original"})
    assert r.status_code == 201

    r = client.patch(f"/config/erp/skus/{codigo}", json={"activo": False, "descripcion": "Editado"})
    assert r.status_code == 200
    assert r.json()["activo"] is False
    assert r.json()["descripcion"] == "Editado"

    sku_db = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo, MaestroSKU.tenant_id == tenant_a)).first()
    assert sku_db.activo is False
    assert sku_db.descripcion == "Editado"


def test_patch_sku_campo_omitido_no_lo_toca(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    codigo = f"SKU-MANUAL-{uuid.uuid4().hex[:8]}"
    r = client.post("/config/erp/skus", json={"codigo_sku": codigo, "descripcion": "Original"})
    assert r.status_code == 201

    r = client.patch(f"/config/erp/skus/{codigo}", json={"activo": False})
    assert r.status_code == 200
    assert r.json()["descripcion"] == "Original"  # no se mandó, no se toca


def test_patch_sku_puede_completar_el_perfil_de_tiempos(client, db, tenant_a, gerente_a):
    """Fase AC: gap real preexistente -- este PATCH sólo permitía editar
    activo/descripcion, ni siquiera tiempo_ideal_seg (antes tiempo_ciclo_teorico)
    era editable después del alta, sólo vía bulk-CSV que pisa todo junto."""
    autenticar_como(gerente_a.id)
    codigo = f"SKU-MANUAL-{uuid.uuid4().hex[:8]}"
    r = client.post("/config/erp/skus", json={"codigo_sku": codigo, "descripcion": "Original"})
    assert r.status_code == 201
    assert r.json()["tiempo_lento_seg"] is None  # perfil incompleto al alta

    r = client.patch(f"/config/erp/skus/{codigo}", json={"tiempo_lento_seg": 280.0, "tiempo_alerta_seg": 300.0})
    assert r.status_code == 200
    body = r.json()
    assert body["tiempo_ideal_seg"] == 240.0  # default, no se tocó
    assert body["tiempo_lento_seg"] == 280.0
    assert body["tiempo_alerta_seg"] == 300.0


def test_patch_sku_perfil_invertido_devuelve_400(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    codigo = f"SKU-MANUAL-{uuid.uuid4().hex[:8]}"
    r = client.post("/config/erp/skus", json={"codigo_sku": codigo, "descripcion": "Original"})
    assert r.status_code == 201

    r = client.patch(f"/config/erp/skus/{codigo}", json={"tiempo_lento_seg": 300.0, "tiempo_alerta_seg": 280.0})
    assert r.status_code == 400

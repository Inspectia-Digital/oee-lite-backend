"""Fase AC: rediseño completo de umbrales/tolerancias, a pedido explícito
de Green Mills tras usar el sistema ("hay que simplificar la lógica,
hacerla robusta, clara para el usuario, con una UX comprensible"). El
modelo de 4 niveles de las Fases Q/R (Estación, Línea, Tenant, más una
rama % aparte para SKU) se reemplaza por UN solo concepto en TODA la
cascada: un "perfil de tiempos" son siempre 3 números en segundos
(ideal, lento, alerta), nunca porcentaje.

Cascada final: SKU × Estación (override puntual) > SKU genérico > Línea
(piso obligatorio, siempre completo). Ver app/core/clasificacion.py.

Este archivo prueba la cascada END-TO-END con los 3 niveles presentes a
la vez (para confirmar el ORDEN de prioridad real, no sólo cada nivel
aislado -- eso ya lo cubren test_fase_ab_orden_umbrales.py y
test_fase_r_tolerancia_tiempo_sku.py) y la carga masiva por CSV con el
perfil completo.
"""
import io
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import (
    Estacion, EstadoOrden, Linea, LiteEventoProduccion, MaestroSKU,
    OrdenProduccion, Planta, SkuTiempoEstacion,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AC")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    # Piso de línea deliberadamente MUY distinto de cualquier otro nivel
    # -- si algo cayera mal la cascada, se notaría enseguida en el estado.
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea AC", tiempo_ideal_seg=1, tiempo_lento_seg=2, tiempo_alerta_seg=3)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación AC", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    return planta, linea, estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _postear_evento(client, credencial, estacion_id, ts):
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id), "timestamp": ts.isoformat()},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    return r


def test_cascada_completa_override_gana_a_sku_generico_que_gana_a_linea(client, db, tenant_a, gerente_a):
    """Los 3 niveles cargados a la vez, cada uno con un perfil bien
    distinto -- confirma el ORDEN real: SKU×Estación > SKU > Línea."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)

    codigo_sku = f"SKU-AC-{uuid.uuid4().hex[:8]}"
    sku = MaestroSKU(
        tenant_id=tenant_a, codigo_sku=codigo_sku, descripcion="SKU cascada completa",
        tiempo_ideal_seg=10, tiempo_lento_seg=15, tiempo_alerta_seg=20,
    )
    db.add(sku)
    db.commit()

    db.add(SkuTiempoEstacion(
        tenant_id=tenant_a, sku_fk=codigo_sku, estacion_id=estacion.id,
        tiempo_ideal_seg=50, tiempo_lento_seg=80, tiempo_alerta_seg=100, activo=True,
    ))
    orden = OrdenProduccion(
        tenant_id=tenant_a, id_orden=f"OP-AC-{uuid.uuid4().hex[:8]}", linea_id=linea.id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=codigo_sku,
    )
    db.add(orden)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    _postear_evento(client, credencial, estacion.id, ahora)
    # 70s: > línea (3) y > SKU genérico (20) de sobra -- si cualquiera de
    # esos dos ganara, sería ALERTA. Con el override (lento=80) sigue OPTIMO.
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=70))

    eventos = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id)).order_by(LiteEventoProduccion.timestamp)
    ).all()
    assert eventos[1].estado == "OPTIMO"
    assert eventos[1].tiempo_ideal_seg == 50.0  # snapshot: el override, no el genérico ni el piso


def test_cascada_sku_generico_gana_a_linea_sin_override(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)

    codigo_sku = f"SKU-AC2-{uuid.uuid4().hex[:8]}"
    sku = MaestroSKU(
        tenant_id=tenant_a, codigo_sku=codigo_sku, descripcion="SKU sin override",
        tiempo_ideal_seg=10, tiempo_lento_seg=15, tiempo_alerta_seg=20,
    )
    db.add(sku)
    db.commit()
    orden = OrdenProduccion(
        tenant_id=tenant_a, id_orden=f"OP-AC2-{uuid.uuid4().hex[:8]}", linea_id=linea.id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=codigo_sku,
    )
    db.add(orden)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    _postear_evento(client, credencial, estacion.id, ahora)
    # 12s: > línea (3, sería ALERTA de sobra) pero < lento del SKU (15) -> OPTIMO.
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=12))

    eventos = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id)).order_by(LiteEventoProduccion.timestamp)
    ).all()
    assert eventos[1].estado == "OPTIMO"
    assert eventos[1].tiempo_ideal_seg == 10.0


def test_cascada_sin_sku_resuelto_usa_el_piso_de_linea(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    _postear_evento(client, credencial, estacion.id, ahora)
    # 5s: > tiempo_alerta_seg de línea (3) -> ALERTA.
    _postear_evento(client, credencial, estacion.id, ahora + timedelta(seconds=5))

    eventos = db.exec(
        select(LiteEventoProduccion).where(LiteEventoProduccion.id_estacion == str(estacion.id)).order_by(LiteEventoProduccion.timestamp)
    ).all()
    assert eventos[1].estado == "ALERTA"
    assert eventos[1].tiempo_ideal_seg == 1.0


# ========================================================
# Carga masiva por CSV con perfil completo (/config/erp/skus/bulk)
# ========================================================

def test_bulk_csv_carga_perfil_completo(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    codigo = f"SKU-BULK-{uuid.uuid4().hex[:8]}"
    csv = (
        "codigo_sku,descripcion,tiempo_ideal_seg,tiempo_lento_seg,tiempo_alerta_seg\n"
        f"{codigo},Producto bulk,100,150,200\n"
    ).encode("utf-8")

    r = client.post("/config/erp/skus/bulk", files={"file": ("skus.csv", csv, "text/csv")})
    assert r.status_code == 200
    assert r.json()["mensaje"].startswith("Se procesaron 1")

    sku_db = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo, MaestroSKU.tenant_id == tenant_a)).first()
    assert sku_db.tiempo_ideal_seg == 100.0
    assert sku_db.tiempo_lento_seg == 150.0
    assert sku_db.tiempo_alerta_seg == 200.0


def test_bulk_csv_lento_alerta_opcionales_quedan_perfil_incompleto(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    codigo = f"SKU-BULK2-{uuid.uuid4().hex[:8]}"
    csv = f"codigo_sku,descripcion,tiempo_ideal_seg\n{codigo},Sólo ideal,100\n".encode("utf-8")

    r = client.post("/config/erp/skus/bulk", files={"file": ("skus.csv", csv, "text/csv")})
    assert r.status_code == 200

    sku_db = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo, MaestroSKU.tenant_id == tenant_a)).first()
    assert sku_db.tiempo_ideal_seg == 100.0
    assert sku_db.tiempo_lento_seg is None
    assert sku_db.tiempo_alerta_seg is None


def test_bulk_csv_perfil_invertido_devuelve_400_sin_crear_nada(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    codigo = f"SKU-BULK3-{uuid.uuid4().hex[:8]}"
    csv = (
        "codigo_sku,descripcion,tiempo_ideal_seg,tiempo_lento_seg,tiempo_alerta_seg\n"
        f"{codigo},Perfil roto,100,300,200\n"
    ).encode("utf-8")

    r = client.post("/config/erp/skus/bulk", files={"file": ("skus.csv", csv, "text/csv")})
    assert r.status_code == 400

    sku_db = db.exec(select(MaestroSKU).where(MaestroSKU.codigo_sku == codigo, MaestroSKU.tenant_id == tenant_a)).first()
    assert sku_db is None  # el rollback del endpoint no deja nada a medio crear

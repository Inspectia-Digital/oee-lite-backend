"""Fase R (feedback de producto sobre la app en uso):

- sku_tiempo_estacion: override opcional del tiempo ideal de un SKU en una
  Estación puntual (CRUD + efecto real en /api/lite/scans). Antes
  MaestroSKU.tiempo_ciclo_teorico era el único valor posible, sin importar
  qué estación procesara el SKU.

Fase AC (rediseño de umbrales/tolerancias): el override ahora es un
PERFIL completo (tiempo_ideal_seg/tiempo_lento_seg/tiempo_alerta_seg, los
3 siempre juntos y NOT NULL -- antes era 1 solo campo + la tolerancia %
heredable Estación>Línea>Tenant aplicada aparte). La cascada de
tolerancia % heredable que este archivo testeaba originalmente fue
RETIRADA por completo -- ver test_fase_ac_perfil_tiempos.py para el
modelo nuevo (SKU×Estación > SKU > Línea, siempre en segundos, nunca %).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import (
    Estacion, LiteEventoProduccion, MaestroSKU, OrdenProduccion, EstadoOrden,
    ParadaDetectada, Planta, Linea, SkuTiempoEstacion, TipoProduccion,
)
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id, tipo_produccion=TipoProduccion.DISCRETA):
    planta = Planta(tenant_id=tenant_id, nombre="Planta R")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea R", tipo_produccion=tipo_produccion)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Armadora", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    return planta, linea, estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _crear_orden_en_progreso(db, tenant_id, linea_id, sku=None):
    if sku is None:
        sku = MaestroSKU(
            tenant_id=tenant_id, codigo_sku=f"SKU-{uuid.uuid4().hex[:8]}",
            descripcion="SKU de prueba", tiempo_ideal_seg=10.0, unidades_por_ciclo=1,
        )
        db.add(sku)
        db.commit()

    orden = OrdenProduccion(
        tenant_id=tenant_id, id_orden=f"OP-{uuid.uuid4().hex[:8]}", linea_id=linea_id,
        estado=EstadoOrden.EN_PROGRESO, sku_fk=sku.codigo_sku,
    )
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden, sku


def _postear_evento(client, credencial, estacion_id, ts):
    return client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id), "timestamp": ts.isoformat()},
        headers={"X-Device-Key": credencial},
    )


def _estado_ultimo_evento(db, estacion_id):
    eventos = db.exec(
        select(LiteEventoProduccion)
        .where(LiteEventoProduccion.id_estacion == str(estacion_id))
        .order_by(LiteEventoProduccion.timestamp)
    ).all()
    return eventos


# ========================================================
# CRUD: /config/erp/skus/{codigo_sku}/tiempos-estacion/
# ========================================================

def test_crear_tiempo_sku_estacion(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 18.5, "tiempo_lento_seg": 22.0, "tiempo_alerta_seg": 25.0},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["tiempo_ideal_seg"] == 18.5
    assert body["tiempo_lento_seg"] == 22.0
    assert body["tiempo_alerta_seg"] == 25.0
    assert body["activo"] is True
    assert body["estacion_id"] == str(estacion.id)

    fila = db.exec(select(SkuTiempoEstacion).where(SkuTiempoEstacion.id == uuid.UUID(body["id"]))).first()
    assert fila is not None
    assert fila.tenant_id == tenant_a


def test_crear_tiempo_sku_estacion_perfil_invertido_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 18.5, "tiempo_lento_seg": 25.0, "tiempo_alerta_seg": 22.0},
    )
    assert r.status_code == 400


def test_crear_tiempo_sku_estacion_sku_inexistente_404(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)

    r = client.post(
        "/config/erp/skus/NO-EXISTE/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 10.0, "tiempo_lento_seg": 12.0, "tiempo_alerta_seg": 14.0},
    )
    assert r.status_code == 404


def test_crear_tiempo_sku_estacion_de_otro_tenant_devuelve_400(client, db, tenant_a, gerente_a, tenant_b):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)

    planta_b = Planta(tenant_id=tenant_b, nombre="Planta B")
    db.add(planta_b)
    db.commit()
    db.refresh(planta_b)
    linea_b = Linea(tenant_id=tenant_b, planta_id=planta_b.id, nombre="Línea B")
    db.add(linea_b)
    db.commit()
    db.refresh(linea_b)
    estacion_b = Estacion(tenant_id=tenant_b, nombre="Est B", tipo="sensor", linea_id=linea_b.id)
    db.add(estacion_b)
    db.commit()
    db.refresh(estacion_b)

    autenticar_como(gerente_a.id)
    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion_b.id), "tiempo_ideal_seg": 10.0, "tiempo_lento_seg": 12.0, "tiempo_alerta_seg": 14.0},
    )
    assert r.status_code == 400  # la estación es de otro tenant


def test_crear_tiempo_sku_estacion_duplicado_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r1 = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 10.0, "tiempo_lento_seg": 12.0, "tiempo_alerta_seg": 14.0},
    )
    assert r1.status_code == 201

    r2 = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 20.0, "tiempo_lento_seg": 24.0, "tiempo_alerta_seg": 28.0},
    )
    assert r2.status_code == 409


def test_crear_tiempo_sku_estacion_requiere_gerencia(client, db, tenant_a):
    from app.models.domain import RolUsuario

    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    operario = crear_usuario(db, tenant_a, RolUsuario.OPERARIO)
    autenticar_como(operario.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 10.0, "tiempo_lento_seg": 12.0, "tiempo_alerta_seg": 14.0},
    )
    assert r.status_code == 403


def test_listar_tiempos_sku_estacion_excluye_inactivos_por_default(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 10.0, "tiempo_lento_seg": 12.0, "tiempo_alerta_seg": 14.0},
    )
    tiempo_id = r.json()["id"]
    r = client.delete(f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/{tiempo_id}")
    assert r.status_code == 200

    r = client.get(f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/")
    assert r.status_code == 200
    assert r.json() == []

    r = client.get(f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/?incluir_inactivos=true")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["activo"] is False


def test_patch_tiempo_sku_estacion_persiste_y_campo_omitido_no_lo_toca(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 10.0, "tiempo_lento_seg": 12.0, "tiempo_alerta_seg": 14.0},
    )
    tiempo_id = r.json()["id"]

    r = client.patch(f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/{tiempo_id}", json={"tiempo_ideal_seg": 8.0})
    assert r.status_code == 200
    assert r.json()["tiempo_ideal_seg"] == 8.0
    assert r.json()["tiempo_lento_seg"] == 12.0  # no se mandó, no se toca...
    assert r.json()["activo"] is True  # ...ni tampoco esto


def test_patch_tiempo_sku_estacion_perfil_invertido_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 10.0, "tiempo_lento_seg": 12.0, "tiempo_alerta_seg": 14.0},
    )
    tiempo_id = r.json()["id"]

    # Patch deja lento (20) >= alerta (14, sin tocar) -> perfil resultante inválido.
    r = client.patch(f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/{tiempo_id}", json={"tiempo_lento_seg": 20.0})
    assert r.status_code == 400


def test_delete_tiempo_sku_estacion_es_baja_logica(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ideal_seg": 10.0, "tiempo_lento_seg": 12.0, "tiempo_alerta_seg": 14.0},
    )
    tiempo_id = r.json()["id"]

    r = client.delete(f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/{tiempo_id}")
    assert r.status_code == 200

    fila = db.exec(select(SkuTiempoEstacion).where(SkuTiempoEstacion.id == uuid.UUID(tiempo_id))).first()
    assert fila is not None  # sigue existiendo -- baja lógica, no DELETE físico
    assert fila.activo is False


# ========================================================
# Efecto real en /api/lite/scans -- cascada SKU×Estación > SKU > Línea (Fase AC)
# ========================================================

def test_override_sku_estacion_pisa_perfil_generico_del_sku(client, db, tenant_a, gerente_a):
    """Un SKU puede tardar distinto según la estación que lo procesa -- si
    hay un override cargado para (SKU, Estación), ese manda sobre el
    perfil genérico de MaestroSKU."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    # Genérico: ~2s/unidad, con perfil completo propio (para que el test
    # distinga claramente "usó el override" de "usó el genérico" de "cayó
    # al piso de línea", que son 3 fuentes bien distintas ahora).
    sku.tiempo_ideal_seg = 2.0
    sku.tiempo_lento_seg = 2.3
    sku.tiempo_alerta_seg = 2.5
    db.add(sku)

    # En ESTA estación puntual el mismo SKU tarda mucho más (proceso más
    # lento en esta máquina en particular).
    override = SkuTiempoEstacion(
        tenant_id=tenant_a, sku_fk=sku.codigo_sku, estacion_id=estacion.id,
        tiempo_ideal_seg=20.0, tiempo_lento_seg=25.0, tiempo_alerta_seg=30.0, activo=True,
    )
    db.add(override)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    # 21s de hueco: con el genérico (alerta=2.5s) sería ALERTA y generaría
    # una parada. Con el override (alerta=30s) tiene que seguir OPTIMO.
    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "OPTIMO"

    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 0


def test_override_inactivo_se_ignora_y_cae_al_generico(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ideal_seg = 2.0
    sku.tiempo_lento_seg = 2.3
    sku.tiempo_alerta_seg = 2.5
    db.add(sku)

    override = SkuTiempoEstacion(
        tenant_id=tenant_a, sku_fk=sku.codigo_sku, estacion_id=estacion.id,
        tiempo_ideal_seg=20.0, tiempo_lento_seg=25.0, tiempo_alerta_seg=30.0,
        activo=False,  # desactivado -> no debe aplicarse
    )
    db.add(override)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "ALERTA"  # override inactivo no cuenta -> genérico (alerta=2.5s)


def test_sin_override_cae_al_perfil_generico_del_sku(client, db, tenant_a, gerente_a):
    """Contraparte: sin ninguna fila en sku_tiempo_estacion, el
    comportamiento tiene que caer al perfil genérico del SKU (si está
    completo) -- nunca directo al piso de línea."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ideal_seg = 2.0
    sku.tiempo_lento_seg = 2.3
    sku.tiempo_alerta_seg = 2.5
    db.add(sku)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "ALERTA"


def test_sku_con_perfil_incompleto_cae_entero_al_piso_de_linea(client, db, tenant_a, gerente_a):
    """Regla central del rediseño (Fase AC): un SKU con sólo el ideal
    cargado (sin lento/alerta) es un perfil INCOMPLETO -- el evento cae
    ENTERO al piso de Línea, nunca mezcla el ideal del SKU con el
    lento/alerta de otra fuente."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    linea.tiempo_ideal_seg = 5.0
    linea.tiempo_lento_seg = 50.0
    linea.tiempo_alerta_seg = 60.0
    db.add(linea)
    db.commit()

    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ideal_seg = 2.0  # sólo el ideal -- lento/alerta quedan None
    db.add(sku)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    # 55s: si usara el ideal del SKU (2.0) con CUALQUIER tolerancia sería
    # ALERTA de sobra. Si cae correctamente al piso de línea (lento=50,
    # alerta=60), tiene que dar LENTO.
    ts2 = ahora + timedelta(seconds=55)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "LENTO"

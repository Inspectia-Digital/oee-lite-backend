"""Fase R (feedback de producto sobre la app en uso):

- sku_tiempo_estacion: override opcional del tiempo ideal de un SKU en una
  Estación puntual (CRUD + efecto real en /api/lite/scans). Antes
  MaestroSKU.tiempo_ciclo_teorico era el único valor posible, sin importar
  qué estación procesara el SKU.
- tolerancia_lento_pct / tolerancia_alerta_pct heredable Estación > Línea >
  Tenant (antes un único valor fijo por tenant, sin importar qué línea o
  estación reportara el evento).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import (
    Estacion, LiteEventoProduccion, MaestroSKU, OrdenProduccion, EstadoOrden,
    ParadaDetectada, Planta, Linea, SkuTiempoEstacion, Tenant, TipoProduccion,
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
            descripcion="SKU de prueba", tiempo_ciclo_teorico=10.0, unidades_por_ciclo=1,
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
        json={"estacion_id": str(estacion.id), "tiempo_ciclo_teorico": 18.5},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["tiempo_ciclo_teorico"] == 18.5
    assert body["activo"] is True
    assert body["estacion_id"] == str(estacion.id)

    fila = db.exec(select(SkuTiempoEstacion).where(SkuTiempoEstacion.id == uuid.UUID(body["id"]))).first()
    assert fila is not None
    assert fila.tenant_id == tenant_a


def test_crear_tiempo_sku_estacion_sku_inexistente_404(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)

    r = client.post(
        "/config/erp/skus/NO-EXISTE/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ciclo_teorico": 10.0},
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
        json={"estacion_id": str(estacion_b.id), "tiempo_ciclo_teorico": 10.0},
    )
    assert r.status_code == 400  # la estación es de otro tenant


def test_crear_tiempo_sku_estacion_duplicado_devuelve_409(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r1 = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ciclo_teorico": 10.0},
    )
    assert r1.status_code == 201

    r2 = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ciclo_teorico": 20.0},
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
        json={"estacion_id": str(estacion.id), "tiempo_ciclo_teorico": 10.0},
    )
    assert r.status_code == 403


def test_listar_tiempos_sku_estacion_excluye_inactivos_por_default(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ciclo_teorico": 10.0},
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
        json={"estacion_id": str(estacion.id), "tiempo_ciclo_teorico": 10.0},
    )
    tiempo_id = r.json()["id"]

    r = client.patch(f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/{tiempo_id}", json={"tiempo_ciclo_teorico": 15.0})
    assert r.status_code == 200
    assert r.json()["tiempo_ciclo_teorico"] == 15.0
    assert r.json()["activo"] is True  # no se mandó, no se toca


def test_delete_tiempo_sku_estacion_es_baja_logica(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    _, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/",
        json={"estacion_id": str(estacion.id), "tiempo_ciclo_teorico": 10.0},
    )
    tiempo_id = r.json()["id"]

    r = client.delete(f"/config/erp/skus/{sku.codigo_sku}/tiempos-estacion/{tiempo_id}")
    assert r.status_code == 200

    fila = db.exec(select(SkuTiempoEstacion).where(SkuTiempoEstacion.id == uuid.UUID(tiempo_id))).first()
    assert fila is not None  # sigue existiendo -- baja lógica, no DELETE físico
    assert fila.activo is False


# ========================================================
# Efecto real en /api/lite/scans
# ========================================================

def test_override_sku_estacion_pisa_tiempo_generico_del_sku(client, db, tenant_a, gerente_a):
    """Un SKU puede tardar distinto según la estación que lo procesa -- si
    hay un override cargado para (SKU, Estación), ese manda sobre
    MaestroSKU.tiempo_ciclo_teorico (genérico)."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ciclo_teorico = 2.0  # genérico: ~2s/unidad en cualquier estación
    db.add(sku)

    # En ESTA estación puntual el mismo SKU tarda mucho más (proceso más
    # lento en esta máquina en particular).
    override = SkuTiempoEstacion(
        tenant_id=tenant_a, sku_fk=sku.codigo_sku, estacion_id=estacion.id,
        tiempo_ciclo_teorico=20.0, activo=True,
    )
    db.add(override)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    # 21s de hueco: con el genérico (2.0s -> t_alerta=2.5s con tolerancia
    # default 1.25) sería ALERTA y generaría una parada. Con el override
    # (20s -> t_alerta=25s) tiene que seguir OPTIMO.
    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "OPTIMO"

    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 0


def test_override_inactivo_se_ignora_y_cae_al_generico(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ciclo_teorico = 2.0
    db.add(sku)

    override = SkuTiempoEstacion(
        tenant_id=tenant_a, sku_fk=sku.codigo_sku, estacion_id=estacion.id,
        tiempo_ciclo_teorico=20.0, activo=False,  # desactivado -> no debe aplicarse
    )
    db.add(override)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "ALERTA"  # override inactivo no cuenta -> genérico (2.0s)


def test_sin_override_cae_al_tiempo_generico_del_sku(client, db, tenant_a, gerente_a):
    """Contraparte: sin ninguna fila en sku_tiempo_estacion, el
    comportamiento tiene que ser exactamente el de antes de Fase R."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ciclo_teorico = 2.0
    db.add(sku)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "ALERTA"


# ---------- Tolerancia % heredable Estación > Línea > Tenant ----------

def test_tolerancia_lee_del_tenant_no_es_un_fallback_hardcodeado(client, db, tenant_a, gerente_a):
    """Sin override en Línea ni Estación, tiene que usar la tolerancia REAL
    del Tenant -- no los literales 1.15/1.25 del fallback de default (que
    sólo deben aplicar si ni siquiera hay fila de Tenant)."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ciclo_teorico = 10.0
    db.add(sku)

    tenant = db.get(Tenant, tenant_a)
    tenant.tolerancia_lento_pct = 5.0  # tenant muy permisivo: LENTO recién a partir de 50s
    tenant.tolerancia_alerta_pct = 6.0  # y ALERTA recién a partir de 60s
    db.add(tenant)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    # 20s: con el default de sistema (1.15/1.25 * 10 = 11.5/12.5) sería
    # ALERTA (y generaría una parada); con la tolerancia real del tenant
    # (50/60) tiene que seguir OPTIMO.
    ts2 = ahora + timedelta(seconds=20)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "OPTIMO"
    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 0


def test_tolerancia_de_linea_pisa_al_tenant_si_estacion_no_define(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ciclo_teorico = 10.0
    db.add(sku)

    # Línea muy permisiva (tenant se queda en el default 1.15/1.25).
    linea.tolerancia_lento_pct = 5.0
    linea.tolerancia_alerta_pct = 6.0
    db.add(linea)
    db.commit()
    # La Estación no define nada -- tiene que heredar de la Línea, no
    # saltearla directo al Tenant.

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    # 20s: > el default de sistema (11.5/12.5, sería ALERTA) pero muy por
    # debajo de lo que permite la Línea (50/60) -> tiene que seguir OPTIMO.
    ts2 = ahora + timedelta(seconds=20)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "OPTIMO"
    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 0


def test_tolerancia_de_estacion_pisa_a_la_de_linea(client, db, tenant_a, gerente_a):
    """Estación > Línea en la cascada -- si ambas están cargadas, gana la
    de la Estación."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ciclo_teorico = 10.0
    db.add(sku)

    linea.tolerancia_lento_pct = 1.0  # línea estricta: LENTO/ALERTA a partir de 10s/11s
    linea.tolerancia_alerta_pct = 1.1
    estacion.tolerancia_lento_pct = 5.0  # esta estación puntual es mucho más permisiva
    estacion.tolerancia_alerta_pct = 6.0
    db.add(linea)
    db.add(estacion)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    # 20s: > umbral de la línea (11s, sería ALERTA) pero muy por debajo del
    # de la estación (60s) -- si ganara la línea sería ALERTA; gana la
    # estación -> OPTIMO.
    ts2 = ahora + timedelta(seconds=20)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "OPTIMO"
    paradas = db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion.id)).all()
    assert len(paradas) == 0


def test_tolerancia_lento_y_alerta_heredan_por_separado(client, db, tenant_a, gerente_a):
    """tolerancia_lento_pct y tolerancia_alerta_pct son dos cascadas
    independientes -- una Estación puede pisar sólo una de las dos."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id)
    sku.tiempo_ciclo_teorico = 10.0
    db.add(sku)

    # Sólo se pisa tolerancia_lento_pct a nivel Estación; alerta sigue en
    # el default del tenant (1.25 -> t_alerta=12.5s).
    estacion.tolerancia_lento_pct = 1.05  # LENTO ya a partir de 10.5s
    db.add(estacion)
    db.commit()

    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    # 11s: > t_lento de la estación (10.5s) pero < t_alerta heredado del
    # tenant (12.5s) -> LENTO.
    ts2 = ahora + timedelta(seconds=11)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _estado_ultimo_evento(db, estacion.id)
    assert eventos[1].estado == "LENTO"

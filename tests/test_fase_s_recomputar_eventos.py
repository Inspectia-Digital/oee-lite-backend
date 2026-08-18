"""Fase S: /config/estaciones/{id}/recomputar-eventos/.

El perfil de tiempos (SKU×Estación, SKU genérico, o el piso de Línea --
Fase AC) sólo se lee en /api/lite/scans, en el momento exacto del ping --
cargar un override o editar un perfil DESPUÉS de que ya llegaron eventos
no cambia nada retroactivamente por sí solo (snapshot inmutable). Este
recómputo reaplica la config vigente sobre eventos ya persistidos.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import (
    Estacion, EstadoOrden, EstadoParada, Linea, LiteEventoProduccion,
    MaestroSKU, MotivoParada, OrdenProduccion, ParadaDetectada, Planta,
    RolUsuario, SkuTiempoEstacion, TipoParada, TipoProduccion,
)
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id, tipo_produccion=TipoProduccion.DISCRETA):
    planta = Planta(tenant_id=tenant_id, nombre="Planta S")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea S", tipo_produccion=tipo_produccion)
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


def _crear_orden_en_progreso(db, tenant_id, linea_id, tiempo_ideal_seg=10.0, tiempo_lento_seg=None, tiempo_alerta_seg=None):
    sku = MaestroSKU(
        tenant_id=tenant_id, codigo_sku=f"SKU-{uuid.uuid4().hex[:8]}",
        descripcion="SKU de prueba", tiempo_ideal_seg=tiempo_ideal_seg,
        tiempo_lento_seg=tiempo_lento_seg, tiempo_alerta_seg=tiempo_alerta_seg,
        unidades_por_ciclo=1,
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


def _recomputar(client, gerente, estacion_id, desde, hasta):
    autenticar_como(gerente.id)
    return client.post(
        f"/config/estaciones/{estacion_id}/recomputar-eventos/",
        json={"fecha_desde": desde.isoformat(), "fecha_hasta": hasta.isoformat()},
    )


def _eventos(db, estacion_id):
    return db.exec(
        select(LiteEventoProduccion)
        .where(LiteEventoProduccion.id_estacion == str(estacion_id))
        .order_by(LiteEventoProduccion.timestamp)
    ).all()


def _paradas(db, estacion_id):
    return db.exec(select(ParadaDetectada).where(ParadaDetectada.estacion_fk == estacion_id)).all()


def test_recomputar_aplica_override_sku_estacion_cargado_despues_del_evento(client, db, tenant_a, gerente_a):
    """El caso real que motivó Fase S: el override se carga DESPUÉS de que
    el evento ya se ingirió -- no cambia nada solo, hace falta el recómputo."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ideal_seg=2.0, tiempo_lento_seg=2.3, tiempo_alerta_seg=2.5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    # BE-P0-05 (PRD Go-Live Green Mills): fijo, no datetime.now(timezone.utc)
    # -- ahora que recomputar_eventos convierte fecha_desde/fecha_hasta a
    # UTC vía la timezone REAL de la planta (antes datetime.combine naive,
    # ver rango_utc_multi_dia_local), un "ahora" real tomado entre las
    # 00:00 y las 02:59 UTC cae en el día calendario ANTERIOR para una
    # planta en Buenos Aires (UTC-3) -- `hoy = ahora.date()` (UTC) ya no
    # sería el mismo día que `hoy` en la planta, y el recómputo de "hoy"
    # no encontraría el evento. Instante fijo bien lejos de cualquier
    # medianoche (15hs UTC = mediodía en Buenos Aires) para que el test
    # sea determinista en vez de fallar ~3hs por día al azar.
    ahora = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)

    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201
    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _eventos(db, estacion.id)
    assert eventos[1].tiempo_ideal_seg == 2.0
    assert eventos[1].estado == "ALERTA"  # 21s contra t_alerta=2.5s (perfil genérico del SKU)
    parada = _paradas(db, estacion.id)[0]
    assert parada.duracion_segundos == 18.5  # 21 - 2.5

    # Override cargado DESPUÉS -- nada lo refleja todavía sin recomputar.
    db.add(SkuTiempoEstacion(
        tenant_id=tenant_a, sku_fk=sku.codigo_sku, estacion_id=estacion.id,
        tiempo_ideal_seg=20.0, tiempo_lento_seg=23.0, tiempo_alerta_seg=25.0, activo=True,
    ))
    db.commit()
    assert _eventos(db, estacion.id)[1].tiempo_ideal_seg == 2.0

    hoy = ahora.date()
    r = _recomputar(client, gerente_a, estacion.id, hoy, hoy)
    assert r.status_code == 200
    body = r.json()
    assert body["eventos_recomputados"] >= 1

    # El recómputo corre en OTRA sesión (la del request); esta sesión de
    # test tiene los objetos viejos en su identity map -- hay que
    # invalidarlos para que la siguiente lectura vaya de nuevo a la base.
    db.expire_all()
    eventos = _eventos(db, estacion.id)
    assert eventos[1].tiempo_ideal_seg == 20.0
    assert eventos[1].estado == "OPTIMO"  # 21s < t_alerta(25) con el override


def test_recomputar_crea_parada_nueva_cuando_se_endurece_el_perfil_del_sku(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ideal_seg=10.0, tiempo_lento_seg=11.5, tiempo_alerta_seg=12.5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    # BE-P0-05 (PRD Go-Live Green Mills): fijo, no datetime.now(timezone.utc)
    # -- ahora que recomputar_eventos convierte fecha_desde/fecha_hasta a
    # UTC vía la timezone REAL de la planta (antes datetime.combine naive,
    # ver rango_utc_multi_dia_local), un "ahora" real tomado entre las
    # 00:00 y las 02:59 UTC cae en el día calendario ANTERIOR para una
    # planta en Buenos Aires (UTC-3) -- `hoy = ahora.date()` (UTC) ya no
    # sería el mismo día que `hoy` en la planta, y el recómputo de "hoy"
    # no encontraría el evento. Instante fijo bien lejos de cualquier
    # medianoche (15hs UTC = mediodía en Buenos Aires) para que el test
    # sea determinista en vez de fallar ~3hs por día al azar.
    ahora = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)

    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201
    # 11s: con el perfil inicial del SKU (t_lento=11.5, t_alerta=12.5)
    # queda OPTIMO -- ninguna parada todavía.
    ts2 = ahora + timedelta(seconds=11)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    eventos = _eventos(db, estacion.id)
    assert eventos[1].estado == "OPTIMO"
    assert len(_paradas(db, estacion.id)) == 0

    # Endurecemos el perfil del SKU: alerta a partir de 10.5s.
    sku.tiempo_lento_seg = 10.2
    sku.tiempo_alerta_seg = 10.5
    db.add(sku)
    db.commit()

    hoy = ahora.date()
    r = _recomputar(client, gerente_a, estacion.id, hoy, hoy)
    assert r.status_code == 200
    body = r.json()
    assert body["paradas_creadas"] == 1

    db.expire_all()
    eventos = _eventos(db, estacion.id)
    assert eventos[1].estado == "ALERTA"
    paradas = _paradas(db, estacion.id)
    assert len(paradas) == 1
    assert paradas[0].estado == EstadoParada.PENDIENTE
    assert paradas[0].duracion_segundos == 11 - 10.5


def test_recomputar_actualiza_duracion_de_parada_pendiente_existente(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ideal_seg=2.0, tiempo_lento_seg=2.3, tiempo_alerta_seg=2.5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    # BE-P0-05 (PRD Go-Live Green Mills): fijo, no datetime.now(timezone.utc)
    # -- ahora que recomputar_eventos convierte fecha_desde/fecha_hasta a
    # UTC vía la timezone REAL de la planta (antes datetime.combine naive,
    # ver rango_utc_multi_dia_local), un "ahora" real tomado entre las
    # 00:00 y las 02:59 UTC cae en el día calendario ANTERIOR para una
    # planta en Buenos Aires (UTC-3) -- `hoy = ahora.date()` (UTC) ya no
    # sería el mismo día que `hoy` en la planta, y el recómputo de "hoy"
    # no encontraría el evento. Instante fijo bien lejos de cualquier
    # medianoche (15hs UTC = mediodía en Buenos Aires) para que el test
    # sea determinista en vez de fallar ~3hs por día al azar.
    ahora = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)

    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201
    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    parada_original = _paradas(db, estacion.id)[0]
    assert parada_original.duracion_segundos == 18.5  # 21 - 2.5
    id_parada_original = parada_original.id

    # Override que sigue dejando el evento en ALERTA, pero con otra duración.
    db.add(SkuTiempoEstacion(
        tenant_id=tenant_a, sku_fk=sku.codigo_sku, estacion_id=estacion.id,
        tiempo_ideal_seg=5.0, tiempo_lento_seg=6.0, tiempo_alerta_seg=6.25, activo=True,
    ))
    db.commit()

    hoy = ahora.date()
    r = _recomputar(client, gerente_a, estacion.id, hoy, hoy)
    assert r.status_code == 200
    body = r.json()
    assert body["paradas_actualizadas"] == 1
    assert body["paradas_creadas"] == 0

    db.expire_all()
    paradas = _paradas(db, estacion.id)
    assert len(paradas) == 1  # se actualizó la misma, no se creó otra
    assert paradas[0].id == id_parada_original
    assert paradas[0].duracion_segundos == 21 - 6.25


def test_recomputar_nunca_toca_una_parada_ya_clasificada(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ideal_seg=2.0, tiempo_lento_seg=2.3, tiempo_alerta_seg=2.5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    # BE-P0-05 (PRD Go-Live Green Mills): fijo, no datetime.now(timezone.utc)
    # -- ahora que recomputar_eventos convierte fecha_desde/fecha_hasta a
    # UTC vía la timezone REAL de la planta (antes datetime.combine naive,
    # ver rango_utc_multi_dia_local), un "ahora" real tomado entre las
    # 00:00 y las 02:59 UTC cae en el día calendario ANTERIOR para una
    # planta en Buenos Aires (UTC-3) -- `hoy = ahora.date()` (UTC) ya no
    # sería el mismo día que `hoy` en la planta, y el recómputo de "hoy"
    # no encontraría el evento. Instante fijo bien lejos de cualquier
    # medianoche (15hs UTC = mediodía en Buenos Aires) para que el test
    # sea determinista en vez de fallar ~3hs por día al azar.
    ahora = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)

    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201
    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    parada = _paradas(db, estacion.id)[0]
    motivo = MotivoParada(tenant_id=tenant_a, nombre="Corte de luz", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)

    autenticar_como(gerente_a.id)
    r = client.patch(
        f"/supervisor/paradas/{parada.id}/clasificar",
        json={"motivo_fk": str(motivo.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    duracion_original = r.json()["duracion_segundos"]

    # Aflojamos mucho el perfil del SKU -- el hueco ya no calificaría ALERTA.
    sku.tiempo_alerta_seg = 999.0
    db.add(sku)
    db.commit()

    hoy = ahora.date()
    r = _recomputar(client, gerente_a, estacion.id, hoy, hoy)
    assert r.status_code == 200
    body = r.json()
    assert body["paradas_ya_clasificadas_sin_tocar"] == 1
    assert str(parada.id) not in [str(x) for x in body["paradas_pendientes_obsoletas"]]

    db.expire_all()
    parada_db = db.get(ParadaDetectada, parada.id)
    assert parada_db.estado == EstadoParada.CLASIFICADA
    assert parada_db.motivo_fk == motivo.id
    assert parada_db.duracion_segundos == duracion_original  # intacta


def test_recomputar_reporta_parada_pendiente_obsoleta_sin_borrarla(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ideal_seg=2.0, tiempo_lento_seg=2.3, tiempo_alerta_seg=2.5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    # BE-P0-05 (PRD Go-Live Green Mills): fijo, no datetime.now(timezone.utc)
    # -- ahora que recomputar_eventos convierte fecha_desde/fecha_hasta a
    # UTC vía la timezone REAL de la planta (antes datetime.combine naive,
    # ver rango_utc_multi_dia_local), un "ahora" real tomado entre las
    # 00:00 y las 02:59 UTC cae en el día calendario ANTERIOR para una
    # planta en Buenos Aires (UTC-3) -- `hoy = ahora.date()` (UTC) ya no
    # sería el mismo día que `hoy` en la planta, y el recómputo de "hoy"
    # no encontraría el evento. Instante fijo bien lejos de cualquier
    # medianoche (15hs UTC = mediodía en Buenos Aires) para que el test
    # sea determinista en vez de fallar ~3hs por día al azar.
    ahora = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)

    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201
    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    parada_id = _paradas(db, estacion.id)[0].id

    # Aflojamos LOS DOS campos del perfil -- si sólo se afloja alerta, el
    # hueco (21s) sigue superando lento (30) y queda LENTO, no OPTIMO (el
    # objetivo acá es que deje de ser ALERTA sin quedar en otro estado
    # clasificable, para probar el caso "ya no califica").
    sku.tiempo_lento_seg = 30.0
    sku.tiempo_alerta_seg = 40.0  # 40s > 21s: ya no califica
    db.add(sku)
    db.commit()

    hoy = ahora.date()
    r = _recomputar(client, gerente_a, estacion.id, hoy, hoy)
    assert r.status_code == 200
    body = r.json()
    assert str(parada_id) in [str(x) for x in body["paradas_pendientes_obsoletas"]]

    db.expire_all()
    # No hay DELETE de paradas AUTOMATICA en todo el backend -- sigue existiendo, PENDIENTE.
    parada_db = db.get(ParadaDetectada, parada_id)
    assert parada_db is not None
    assert parada_db.estado == EstadoParada.PENDIENTE

    eventos = _eventos(db, estacion.id)
    assert eventos[1].estado == "OPTIMO"


def test_recomputar_respeta_continuidad_de_orden(client, db, tenant_a, gerente_a):
    """Un cambio de orden entre dos eventos consecutivos sigue sin
    clasificarse ni generar/tocar parada, aunque cambie la config."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden1, sku1 = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ideal_seg=2.0, tiempo_lento_seg=2.3, tiempo_alerta_seg=2.5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    # BE-P0-05 (PRD Go-Live Green Mills): fijo, no datetime.now(timezone.utc)
    # -- ahora que recomputar_eventos convierte fecha_desde/fecha_hasta a
    # UTC vía la timezone REAL de la planta (antes datetime.combine naive,
    # ver rango_utc_multi_dia_local), un "ahora" real tomado entre las
    # 00:00 y las 02:59 UTC cae en el día calendario ANTERIOR para una
    # planta en Buenos Aires (UTC-3) -- `hoy = ahora.date()` (UTC) ya no
    # sería el mismo día que `hoy` en la planta, y el recómputo de "hoy"
    # no encontraría el evento. Instante fijo bien lejos de cualquier
    # medianoche (15hs UTC = mediodía en Buenos Aires) para que el test
    # sea determinista en vez de fallar ~3hs por día al azar.
    ahora = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)
    hace_2_horas = ahora - timedelta(hours=2)

    assert _postear_evento(client, credencial, estacion.id, hace_2_horas).status_code == 201

    orden1.estado = EstadoOrden.CERRADA
    db.add(orden1)
    db.commit()
    orden2, sku2 = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ideal_seg=2.0, tiempo_lento_seg=2.3, tiempo_alerta_seg=2.5)

    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201

    eventos_antes = _eventos(db, estacion.id)
    assert eventos_antes[1].estado == "OPTIMO"
    assert len(_paradas(db, estacion.id)) == 0

    # Brutalmente estricto en los dos SKUs -- si la protección de
    # continuidad de orden fallara, cualquiera de los dos generaría ALERTA.
    sku1.tiempo_lento_seg, sku1.tiempo_alerta_seg = 0.001, 0.002
    sku2.tiempo_lento_seg, sku2.tiempo_alerta_seg = 0.001, 0.002
    db.add(sku1)
    db.add(sku2)
    db.commit()

    r = _recomputar(client, gerente_a, estacion.id, hace_2_horas.date(), ahora.date())
    assert r.status_code == 200
    body = r.json()
    assert body["paradas_creadas"] == 0

    db.expire_all()
    eventos_despues = _eventos(db, estacion.id)
    assert eventos_despues[1].estado == "OPTIMO"  # el cambio de orden lo sigue protegiendo
    assert len(_paradas(db, estacion.id)) == 0


def test_recomputar_no_toca_eventos_fuera_del_rango(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    orden, sku = _crear_orden_en_progreso(db, tenant_a, linea.id, tiempo_ideal_seg=2.0, tiempo_lento_seg=2.3, tiempo_alerta_seg=2.5)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    # BE-P0-05 (PRD Go-Live Green Mills): fijo, no datetime.now(timezone.utc)
    # -- ahora que recomputar_eventos convierte fecha_desde/fecha_hasta a
    # UTC vía la timezone REAL de la planta (antes datetime.combine naive,
    # ver rango_utc_multi_dia_local), un "ahora" real tomado entre las
    # 00:00 y las 02:59 UTC cae en el día calendario ANTERIOR para una
    # planta en Buenos Aires (UTC-3) -- `hoy = ahora.date()` (UTC) ya no
    # sería el mismo día que `hoy` en la planta, y el recómputo de "hoy"
    # no encontraría el evento. Instante fijo bien lejos de cualquier
    # medianoche (15hs UTC = mediodía en Buenos Aires) para que el test
    # sea determinista en vez de fallar ~3hs por día al azar.
    ahora = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)

    assert _postear_evento(client, credencial, estacion.id, ahora).status_code == 201
    ts2 = ahora + timedelta(seconds=21)
    assert _postear_evento(client, credencial, estacion.id, ts2).status_code == 201

    db.add(SkuTiempoEstacion(
        tenant_id=tenant_a, sku_fk=sku.codigo_sku, estacion_id=estacion.id,
        tiempo_ideal_seg=20.0, tiempo_lento_seg=23.0, tiempo_alerta_seg=25.0, activo=True,
    ))
    db.commit()

    ayer = (ahora - timedelta(days=1)).date()
    anteayer = (ahora - timedelta(days=2)).date()
    r = _recomputar(client, gerente_a, estacion.id, anteayer, ayer)  # rango que NO cubre "ahora"
    assert r.status_code == 200
    assert r.json()["eventos_recomputados"] == 0

    db.expire_all()
    eventos = _eventos(db, estacion.id)
    assert eventos[1].tiempo_ideal_seg == 2.0  # sin tocar
    assert eventos[1].estado == "ALERTA"


def test_recomputar_requiere_gerencia(client, db, tenant_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    operario = crear_usuario(db, tenant_a, RolUsuario.OPERARIO)
    autenticar_como(operario.id)

    hoy = datetime.now(timezone.utc).date()
    r = client.post(
        f"/config/estaciones/{estacion.id}/recomputar-eventos/",
        json={"fecha_desde": hoy.isoformat(), "fecha_hasta": hoy.isoformat()},
    )
    assert r.status_code == 403


def test_recomputar_fecha_hasta_antes_de_desde_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    hoy = datetime.now(timezone.utc).date()
    ayer = hoy - timedelta(days=1)
    r = _recomputar(client, gerente_a, estacion.id, hoy, ayer)
    assert r.status_code == 400


def test_recomputar_rango_mayor_a_90_dias_devuelve_400(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    hoy = datetime.now(timezone.utc).date()
    hace_100_dias = hoy - timedelta(days=100)
    r = _recomputar(client, gerente_a, estacion.id, hace_100_dias, hoy)
    assert r.status_code == 400


def test_recomputar_estacion_de_otro_tenant_devuelve_404(client, db, tenant_a, gerente_a, tenant_b):
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

    hoy = datetime.now(timezone.utc).date()
    r = _recomputar(client, gerente_a, estacion_b.id, hoy, hoy)
    assert r.status_code == 404

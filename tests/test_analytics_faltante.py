"""Fase I: oee-cascada, rendimiento-secuencial, reporte-produccion, command-center/summary."""
import uuid
from datetime import datetime, timedelta, timezone

from app.core.tiempo_planta import fecha_operativa_planta
from app.models.domain import Estacion, Linea, Planta, RolUsuario, Turno
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta I")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    # Fase AC: el perfil de tiempos vive en Línea (Estación ya no tiene
    # uno propio).
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea I", tiempo_ideal_seg=100)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(
        tenant_id=tenant_id, nombre="Estación I", tipo="sensor", linea_id=linea.id,
        posicion_linea=1, activa=True,
    )
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_id, nombre="Full", hora_inicio="00:00:00", hora_fin="23:59:00", linea_id=linea.id)
    db.add(turno)
    db.commit()

    return planta, linea, estacion


def _emitir_key_y_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _emitir_evento(client, credencial, estacion_id):
    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201


# ---------- oee-cascada ----------

def test_cascada_sin_planta_devuelve_ceros(client, db, tenant_a, gerente_a):
    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-cascada/")
    assert r.status_code == 200
    assert r.json()["tiempo_calendario_min"] == 0.0


def test_cascada_etapas_decrecen_monotonicamente(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)
    _emitir_evento(client, credencial, estacion.id)

    autenticar_como(gerente_a.id)
    r = client.get("/analytics/oee-cascada/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    c = r.json()
    assert c["tiempo_calendario_min"] >= c["tiempo_planificado_min"]
    assert c["tiempo_planificado_min"] >= c["tiempo_operativo_min"]
    assert c["tiempo_operativo_min"] >= c["tiempo_neto_min"]
    assert c["tiempo_neto_min"] >= c["tiempo_efectivo_min"]
    assert c["tiempo_efectivo_min"] >= 0


# ---------- rendimiento-secuencial ----------

def test_rendimiento_secuencial_ordenado_por_posicion(client, db, tenant_a, gerente_a):
    planta, linea, estacion1 = _preparar_escenario(db, tenant_a)
    estacion2 = Estacion(tenant_id=tenant_a, nombre="Estación I2", tipo="sensor", linea_id=linea.id, posicion_linea=2)
    db.add(estacion2)
    db.commit()
    db.refresh(estacion2)

    cred1 = _emitir_key_y_credencial(client, gerente_a, estacion1.id)
    cred2 = _emitir_key_y_credencial(client, gerente_a, estacion2.id)
    _emitir_evento(client, cred1, estacion1.id)
    _emitir_evento(client, cred2, estacion2.id)

    autenticar_como(gerente_a.id)
    r = client.get(f"/analytics/rendimiento-secuencial/?linea_id={linea.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    filas = r.json()
    assert [f["posicion_linea"] for f in filas] == sorted(f["posicion_linea"] for f in filas)


def test_rendimiento_secuencial_no_rompe_sin_sku_activo(client, db, tenant_a, gerente_a):
    """Fase AC: Estación ya no tiene ningún campo de umbral propio (antes
    era Optional[int], podía romper la construcción del modelo con un
    500 si quedaba None). Ahora, sin SKU resuelto, el evento cae al piso
    de Línea -- que SIEMPRE tiene un valor (NOT NULL) -- así que el caso
    "nada configurado por SKU" sigue devolviendo un objetivo numérico válido."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)
    _emitir_evento(client, credencial, estacion.id)
    _emitir_evento(client, credencial, estacion.id)  # 2do evento: el 1ro nunca tiene delta_t > 0

    autenticar_como(gerente_a.id)
    r = client.get(f"/analytics/rendimiento-secuencial/?linea_id={linea.id}", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert r.json()[0]["objetivo"] == 100.0  # Línea.tiempo_ideal_seg configurado en _preparar_escenario


def test_rendimiento_secuencial_acepta_fecha_desde_fecha_hasta(client, db, tenant_a, gerente_a):
    """Fase Q (ronda 3): antes sólo aceptaba `fecha` (un día) -- el
    selector de rango ("Últimos N días") del dashboard no tenía ningún
    efecto acá. Un evento de hace 3 días no aparecía si se pedía sólo
    `fecha=hoy`, pero sí tiene que aparecer con fecha_desde/fecha_hasta
    cubriendo el rango."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)
    hace_3_dias = datetime.now(timezone.utc) - timedelta(days=3)
    for i in range(2):
        r = client.post(
            "/api/lite/scans",
            json={
                "event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id),
                "timestamp": (hace_3_dias + timedelta(seconds=30 * i)).isoformat(),
            },
            headers={"X-Device-Key": credencial},
        )
        assert r.status_code == 201

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-secuencial/",
        params={"fecha_desde": (hoy - timedelta(days=6)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["tiempo_ciclo_prom"] == 30.0


# ---------- cuellos-botella ----------

def test_cuellos_botella_esperado_usa_snapshot_del_evento_no_el_piso_de_linea(client, db, tenant_a, gerente_a):
    """Fase Q (ronda 3), reformulado en Fase AC: "esperado" usaba
    estacion.umbral_optimo directo -- desalineado del todo cuando el
    evento resuelve un SKU (tiempo_ideal_seg pasa a ser el ideal del SKU,
    no el piso de la línea -- ver scans.py). Acá la LÍNEA tiene un piso
    deliberadamente DISTINTO del ciclo ideal del SKU (que sí tiene perfil
    completo), para probar que el endpoint usa el snapshot del evento,
    no el piso de línea."""
    from app.models.domain import EstadoOrden, MaestroSKU, OrdenProduccion

    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    linea.tiempo_ideal_seg, linea.tiempo_lento_seg, linea.tiempo_alerta_seg = 999, 1000, 1001  # deliberadamente distinto -- no debería usarse
    db.add(linea)

    # codigo_sku/id_orden con sufijo random (Bug real encontrado corriendo
    # la suite dos veces seguidas contra el mismo Postgres persistente:
    # codigo_sku sigue siendo PK legacy global -- ver nota C1/C2 en
    # MaestroSKU -- un literal fijo choca en la segunda corrida).
    codigo_sku = f"SKU-CB-{uuid.uuid4().hex[:8]}"
    sku = MaestroSKU(
        tenant_id=tenant_a, codigo_sku=codigo_sku, descripcion="Test",
        tiempo_ideal_seg=20.0, tiempo_lento_seg=25.0, tiempo_alerta_seg=30.0,
    )
    db.add(sku)
    db.commit()
    orden = OrdenProduccion(tenant_id=tenant_a, id_orden=f"OP-CB-{uuid.uuid4().hex[:8]}", linea_id=linea.id, estado=EstadoOrden.EN_PROGRESO, sku_fk=sku.codigo_sku)
    db.add(orden)
    db.commit()

    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)
    ahora = datetime.now(timezone.utc)
    for i in range(2):
        r = client.post(
            "/api/lite/scans",
            json={
                "event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id),
                "timestamp": (ahora + timedelta(seconds=20 * i)).isoformat(),
            },
            headers={"X-Device-Key": credencial},
        )
        assert r.status_code == 201

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/cuellos-botella/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    # tiempo_ideal_seg del 2do evento = 20.0 (ideal del SKU, unidades_procesadas=1)
    # -- nunca 999 (el piso de línea).
    assert filas[0]["tiempo_esperado_seg"] == 20.0


def test_cuellos_botella_no_rompe_sin_sku_activo(client, db, tenant_a, gerente_a):
    """Contraparte sin SKU/orden del test anterior -- confirma que el piso
    de Línea (siempre NOT NULL, Fase AC) resuelve un tiempo_esperado_seg
    numérico válido sin necesitar ningún dato adicional."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)
    _emitir_evento(client, credencial, estacion.id)
    _emitir_evento(client, credencial, estacion.id)

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/cuellos-botella/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200


# ---------- reporte-produccion ----------

def test_reporte_produccion_filas_planas_por_fecha(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_key_y_credencial(client, gerente_a, estacion.id)
    _emitir_evento(client, credencial, estacion.id)

    autenticar_como(gerente_a.id)
    # BE-P0-03 (fase EK, bug real encontrado por CI cerca de medianoche
    # UTC): /analytics/reporte-produccion/ trata fecha_desde/fecha_hasta
    # como fecha LOCAL de planta (mismo criterio que el resto de
    # /analytics/*, ver rendimiento-operarios/rendimiento-maquinas) --
    # antes las comparaba naive contra el timestamp UTC del evento, y
    # este test se había escrito a propósito para "alinear" con ESE bug
    # (comentario viejo: "alinear con timestamp UTC"). Ahora que el
    # endpoint es consistente con el resto, el test debe pedir la fecha
    # LOCAL, no la de calendario UTC.
    hoy = fecha_operativa_planta(planta).isoformat()
    r = client.get(
        f"/analytics/reporte-produccion/?fecha_desde={hoy}&fecha_hasta={hoy}",
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["estacion"] == "Estación I"
    assert filas[0]["total_piezas"] == 1


def test_reporte_produccion_rechaza_rango_invertido(client, db, tenant_a, gerente_a):
    planta, _, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/reporte-produccion/?fecha_desde=2026-01-10&fecha_hasta=2026-01-01",
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400


def test_reporte_produccion_rechaza_rango_mayor_a_90_dias(client, db, tenant_a, gerente_a):
    """Fase BU: mismo tope que /analytics/oee-tendencia/ -- sin esto, un
    rango de años trae todos los eventos crudos del tenant a memoria."""
    planta, _, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/reporte-produccion/?fecha_desde=2026-01-01&fecha_hasta=2026-06-01",
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 400
    assert "90" in r.json()["detail"]


def test_reporte_produccion_acepta_rango_de_90_dias(client, db, tenant_a, gerente_a):
    planta, _, _ = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/reporte-produccion/?fecha_desde=2026-01-01&fecha_hasta=2026-04-01",
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200


# ---------- command-center/summary ----------

def test_command_center_gerencia_ve_todas_las_plantas(client, db, tenant_a, gerente_a):
    planta1, _, estacion1 = _preparar_escenario(db, tenant_a)
    planta2 = Planta(tenant_id=tenant_a, nombre="Planta I2")
    db.add(planta2)
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get("/command-center/summary")
    assert r.status_code == 200
    data = r.json()
    nombres = {p["nombre"] for p in data["plantas"]}
    assert {"Planta I", "Planta I2"}.issubset(nombres)
    assert data["infraestructura"]["estaciones_total"] >= 1


def test_command_center_supervisor_solo_ve_su_planta(client, db, tenant_a):
    planta1, _, _ = _preparar_escenario(db, tenant_a)
    planta2 = Planta(tenant_id=tenant_a, nombre="Planta I2")
    db.add(planta2)
    db.commit()
    db.refresh(planta2)

    from app.models.domain import UsuarioPlanta
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=supervisor.id, planta_id=planta1.id))
    db.commit()

    autenticar_como(supervisor.id)
    r = client.get("/command-center/summary")
    assert r.status_code == 200
    nombres = {p["nombre"] for p in r.json()["plantas"]}
    assert nombres == {"Planta I"}


def test_command_center_sin_plantas_asignadas_devuelve_vacio(client, db, tenant_a):
    supervisor = crear_usuario(db, tenant_a, RolUsuario.SUPERVISOR)
    autenticar_como(supervisor.id)
    r = client.get("/command-center/summary")
    assert r.status_code == 200
    assert r.json()["plantas"] == []
    assert r.json()["oee_global"] is None


def test_command_center_respeta_impersonacion_de_superadmin(client, db, tenant_a, tenant_b, superadmin):
    """Fase M: bug real -- usaba usuario.tenant_id (el tenant propio del
    SuperAdmin) en vez de context.tenant_id, así que impersonar otra
    empresa (?tenant_id=) no tenía efecto en este endpoint."""
    _preparar_escenario(db, tenant_a)  # planta del tenant propio del superadmin
    planta_b = Planta(tenant_id=tenant_b, nombre="Planta Impersonada")
    db.add(planta_b)
    db.commit()

    autenticar_como(superadmin.id)

    # Sin impersonar: ve las plantas de su propio tenant (tenant_a).
    r = client.get("/command-center/summary")
    assert r.status_code == 200
    nombres_propios = {p["nombre"] for p in r.json()["plantas"]}
    assert "Planta I" in nombres_propios
    assert "Planta Impersonada" not in nombres_propios

    # Impersonando tenant_b: debe ver la planta de tenant_b, no la propia.
    r = client.get(f"/command-center/summary?tenant_id={tenant_b}")
    assert r.status_code == 200
    nombres_impersonados = {p["nombre"] for p in r.json()["plantas"]}
    assert nombres_impersonados == {"Planta Impersonada"}

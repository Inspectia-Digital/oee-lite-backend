"""Fase EN (PRD_HALLAZGOS_REVISION_DIRECTA.md, hallazgo #1): carga manual
de unidades rechazadas para líneas con Linea.metodo_calidad ==
"por_rechazo" cuya estación de calidad no está instrumentada con scanner
(confirmado con el usuario: la línea de Green Mills que usa este método
necesita carga manual). Ver docstring de RegistroRechazoManual en
domain.py y de registrar_rechazo_manual en operacion.py.

Cubre lo pedido en el plan: crear un registro no toca LiteEventoProduccion;
el cálculo de calidad (_calcular_metricas_oee) suma ambas fuentes; Operario
recibe 403; registrado_por_id queda persistido."""
import uuid
from datetime import datetime, timezone

from app.models.domain import (
    Estacion, Linea, LiteEventoProduccion, MetodoCalidadLinea,
    OrdenProduccion, Planta, RegistroRechazoManual, RolUsuario, Turno,
)
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta EN")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(
        tenant_id=tenant_id, planta_id=planta.id, nombre="Línea EN",
        metodo_calidad=MetodoCalidadLinea.POR_RECHAZO,
        tiempo_ideal_seg=100, tiempo_lento_seg=150, tiempo_alerta_seg=200,
    )
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación EN", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_id, nombre="Full", hora_inicio="00:00:00", hora_fin="23:59:00", linea_id=linea.id)
    db.add(turno)
    db.commit()

    id_orden = f"OP-EN-{uuid.uuid4().hex[:8]}"
    orden = OrdenProduccion(
        tenant_id=tenant_id, id_orden=id_orden, linea_id=linea.id,
        cantidad_esperada=100, estado="abierta",
    )
    db.add(orden)
    db.commit()

    return planta, linea, estacion, id_orden


def _emitir_evento_scan(db, tenant_id, estacion_id, id_orden, unidades_procesadas, unidades_rechazadas):
    """Inserta directo en la tabla (no vía /api/lite/scans) para controlar
    orden_fk explícitamente -- el endpoint de scans lo resuelve solo desde
    la orden ACTIVA de la estación, no lo acepta en el payload."""
    evento = LiteEventoProduccion(
        tenant_id=tenant_id, id_estacion=str(estacion_id), orden_fk=id_orden,
        unidades_procesadas=unidades_procesadas, unidades_rechazadas=unidades_rechazadas,
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None), incluido_oee=True,
    )
    db.add(evento)
    db.commit()
    db.refresh(evento)
    return evento


def _usuario_con_planta(db, tenant_id, rol, planta_id):
    from app.models.domain import UsuarioPlanta
    usuario = crear_usuario(db, tenant_id, rol)
    db.add(UsuarioPlanta(tenant_id=tenant_id, usuario_id=usuario.id, planta_id=planta_id, activo=True))
    db.commit()
    return usuario


def test_encargado_puede_registrar_rechazo_manual(client, db, tenant_a):
    planta, linea, estacion, id_orden = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden, "estacion_id": str(estacion.id), "cantidad_rechazada": 3, "motivo": "Rebabas"},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["cantidad_rechazada"] == 3
    assert data["motivo"] == "Rebabas"
    # registrado_por_id queda persistido con quién lo cargó, no anónimo.
    assert data["registrado_por_id"] == str(encargado.id)


def test_operario_no_puede_registrar_rechazo_manual(client, db, tenant_a):
    planta, linea, estacion, id_orden = _preparar_escenario(db, tenant_a)
    operario_web = _usuario_con_planta(db, tenant_a, RolUsuario.OPERARIO, planta.id)
    autenticar_como(operario_web.id)

    r = client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden, "estacion_id": str(estacion.id), "cantidad_rechazada": 3},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 403


def test_cantidad_rechazada_debe_ser_positiva(client, db, tenant_a):
    planta, linea, estacion, id_orden = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden, "estacion_id": str(estacion.id), "cantidad_rechazada": 0},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 422


def test_registrar_rechazo_manual_no_toca_lite_evento_produccion(client, db, tenant_a):
    planta, linea, estacion, id_orden = _preparar_escenario(db, tenant_a)
    evento = _emitir_evento_scan(db, tenant_a, estacion.id, id_orden, unidades_procesadas=10, unidades_rechazadas=1)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    r = client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden, "estacion_id": str(estacion.id), "cantidad_rechazada": 2},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201

    db.refresh(evento)
    # El evento de scan sigue exactamente como estaba -- la carga manual
    # vive en su propia tabla, nunca pisa el dato del scanner.
    assert evento.unidades_rechazadas == 1
    assert evento.unidades_procesadas == 10


def test_calidad_por_rechazo_suma_scan_y_manual(client, db, tenant_a):
    planta, linea, estacion, id_orden = _preparar_escenario(db, tenant_a)
    # 10 procesadas, 1 rechazada por scan.
    _emitir_evento_scan(db, tenant_a, estacion.id, id_orden, unidades_procesadas=10, unidades_rechazadas=1)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    # +2 rechazadas cargadas a mano -> total rechazadas = 3, buenas = 7/10 = 70%.
    r = client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden, "estacion_id": str(estacion.id), "cantidad_rechazada": 2},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201

    r = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    data = r.json()
    assert data["calidad_pct"] == 70.0


def test_calidad_no_baja_de_cero_si_manual_supera_lo_procesado(client, db, tenant_a):
    planta, linea, estacion, id_orden = _preparar_escenario(db, tenant_a)
    # 5 procesadas, 0 rechazadas por scan.
    _emitir_evento_scan(db, tenant_a, estacion.id, id_orden, unidades_procesadas=5, unidades_rechazadas=0)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    # Carga manual exagerada (dato incompleto/erróneo, Fase 1.3: no se
    # bloquea) -- "buenas" nunca debe mostrarse negativo.
    r = client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden, "estacion_id": str(estacion.id), "cantidad_rechazada": 20},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 201

    r = client.get("/analytics/oee-general/", headers={"X-Sub-Tenant-Id": str(planta.id)})
    assert r.status_code == 200
    assert r.json()["calidad_pct"] == 0.0


def test_listar_rechazos_manuales_por_orden(client, db, tenant_a):
    planta, linea, estacion, id_orden = _preparar_escenario(db, tenant_a)
    encargado = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta.id)
    autenticar_como(encargado.id)

    client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden, "estacion_id": str(estacion.id), "cantidad_rechazada": 1},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden, "estacion_id": str(estacion.id), "cantidad_rechazada": 4},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )

    r = client.get(
        "/supervisor/rechazos-manuales", params={"orden_fk": id_orden},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 2
    assert {f["cantidad_rechazada"] for f in filas} == {1, 4}


def test_registro_de_otro_tenant_no_aparece(client, db, tenant_a, tenant_b):
    planta_a, linea_a, estacion_a, id_orden_a = _preparar_escenario(db, tenant_a)
    planta_b, linea_b, estacion_b, id_orden_b = _preparar_escenario(db, tenant_b)
    encargado_a = _usuario_con_planta(db, tenant_a, RolUsuario.ENCARGADO, planta_a.id)
    encargado_b = _usuario_con_planta(db, tenant_b, RolUsuario.ENCARGADO, planta_b.id)

    autenticar_como(encargado_b.id)
    client.post(
        "/supervisor/rechazos-manuales",
        json={"orden_fk": id_orden_b, "estacion_id": str(estacion_b.id), "cantidad_rechazada": 9},
        headers={"X-Sub-Tenant-Id": str(planta_b.id)},
    )

    autenticar_como(encargado_a.id)
    r = client.get("/supervisor/rechazos-manuales", headers={"X-Sub-Tenant-Id": str(planta_a.id)})
    assert r.status_code == 200
    assert r.json() == []

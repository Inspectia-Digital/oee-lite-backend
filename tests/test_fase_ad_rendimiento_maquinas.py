"""Fase AD (pedido de Green Mills, "gráficos por máquina: real vs.
teórico, por turno"): /analytics/rendimiento-maquinas/.

Confirmado con el usuario (ronda de preguntas de Fase AA/AD): se
reutiliza tiempo_ideal_seg ya grabado en cada evento -- no hace falta un
campo nuevo ni CRUD, es sólo un reporte nuevo sobre datos que ya existen.
Misma metodología asimétrica de Fase V, tercera aplicación.
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import Estacion, Linea, Maquina, MaquinaEstacion, Planta, Turno
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AD", timezone="America/Argentina/Buenos_Aires")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    # Línea con alerta muy holgada (evita generar una parada/ALERTA por
    # accidente en los tests de este archivo) pero lento ajustado -- así
    # el test de rendimiento asimétrico puede disparar LENTO a propósito.
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea AD", tiempo_ideal_seg=5, tiempo_lento_seg=50, tiempo_alerta_seg=1000)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación AD", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    # Turno que cubre TODO el día, todos los días -- evita flakiness por
    # la hora/día real en que corre el test.
    turno = Turno(tenant_id=tenant_id, nombre="Full", hora_inicio="00:00:00", hora_fin="23:59:59", linea_id=linea.id)
    db.add(turno)
    db.commit()
    db.refresh(turno)

    return planta, linea, estacion, turno


def _crear_maquina(db, tenant_id, estacion_id, nombre="Máquina 1"):
    maquina = Maquina(tenant_id=tenant_id, codigo_externo=f"MAQ-{uuid.uuid4().hex[:6]}", nombre=nombre)
    db.add(maquina)
    db.commit()
    db.refresh(maquina)
    db.add(MaquinaEstacion(tenant_id=tenant_id, maquina_id=maquina.id, estacion_id=estacion_id, activo=True))
    db.commit()
    return maquina


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _postear_evento(client, credencial, estacion_id, maquina_id, ts):
    r = client.post(
        "/api/lite/scans",
        json={
            "event_id": str(uuid.uuid4()), "id_estacion": str(estacion_id),
            "maquina_id": str(maquina_id), "timestamp": ts.isoformat(),
        },
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201
    return r


def test_rendimiento_maquinas_agrupa_por_maquina_y_calcula_asimetrico(client, db, tenant_a, gerente_a):
    planta, linea, estacion, turno = _preparar_escenario(db, tenant_a)
    maquina = _crear_maquina(db, tenant_a, estacion.id)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    # 1ro OPTIMO (delta=0, sin evento anterior). 2do LENTO: delta=170s >
    # tiempo_lento_seg(50), ideal=5 -> lentitud=170-5=165.
    _postear_evento(client, credencial, estacion.id, maquina.id, ahora)
    _postear_evento(client, credencial, estacion.id, maquina.id, ahora + timedelta(seconds=170))

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-maquinas/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    fila = filas[0]
    assert fila["maquina_nombre"] == "Máquina 1"
    assert fila["turno_nombre"] == "Full"
    assert fila["eventos_totales"] == 2
    # ideal_total = 5+5=10; lentitud_total=165 -> rendimiento=10/175*100=5.71...->5.7
    assert fila["rendimiento_pct"] == 5.7


def test_rendimiento_maquinas_distingue_dos_maquinas_de_la_misma_estacion(client, db, tenant_a, gerente_a):
    planta, linea, estacion, turno = _preparar_escenario(db, tenant_a)
    maquina1 = _crear_maquina(db, tenant_a, estacion.id, nombre="Máquina 1")
    maquina2 = _crear_maquina(db, tenant_a, estacion.id, nombre="Máquina 2")
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    ahora = datetime.now(timezone.utc) - timedelta(hours=1)
    _postear_evento(client, credencial, estacion.id, maquina1.id, ahora)
    _postear_evento(client, credencial, estacion.id, maquina2.id, ahora + timedelta(seconds=10))

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-maquinas/",
        params={"fecha_desde": (hoy - timedelta(days=1)).isoformat(), "fecha_hasta": hoy.isoformat(), "linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    nombres = sorted(f["maquina_nombre"] for f in r.json())
    assert nombres == ["Máquina 1", "Máquina 2"]


def test_rendimiento_maquinas_sin_maquina_resuelta_no_entra_al_reporte(client, db, tenant_a, gerente_a):
    """Un scan sin maquina_id (hardware que no la informa, Fase E1) se
    acepta igual en /api/lite/scans -- pero no puede atribuirse a ninguna
    máquina en este reporte, así que no aparece (no se inventa una fila)."""
    planta, linea, estacion, turno = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    r = client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 201

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-maquinas/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_rendimiento_maquinas_sin_turno_configurado_usa_sin_turno(client, db, tenant_a, gerente_a):
    planta = Planta(tenant_id=tenant_a, nombre="Planta AD2")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_a, planta_id=planta.id, nombre="Línea AD2")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_a, nombre="Estación AD2", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    # Deliberadamente SIN Turno configurado para esta línea.
    maquina = _crear_maquina(db, tenant_a, estacion.id)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)
    _postear_evento(client, credencial, estacion.id, maquina.id, datetime.now(timezone.utc))

    autenticar_como(gerente_a.id)
    hoy = datetime.now(timezone.utc).date()
    r = client.get(
        "/analytics/rendimiento-maquinas/",
        params={"linea_id": str(linea.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["turno_nombre"] == "Sin turno"

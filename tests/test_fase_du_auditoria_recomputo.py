"""Fase DU (auditoría de backend, P1-02 parcial): recomputar_eventos
persiste un RegistroAuditoria -- antes no quedaba ningún rastro de
quién corrió un recómputo, sobre qué rango, con qué resultado."""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlmodel import select

from app.models.domain import (
    Estacion, EstadoOrden, Linea, MaestroSKU, OrdenProduccion, Planta,
    RegistroAuditoria, TipoProduccion,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta DU")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea DU", tipo_produccion=TipoProduccion.DISCRETA)
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Armadora DU", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    return planta, linea, estacion


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def test_recomputo_persiste_registro_de_auditoria(client, db, tenant_a, gerente_a):
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    hoy = datetime.now(timezone.utc)
    client.post(
        "/api/lite/scans",
        json={"event_id": str(uuid.uuid4()), "id_estacion": str(estacion.id), "timestamp": hoy.isoformat()},
        headers={"X-Device-Key": credencial},
    )

    autenticar_como(gerente_a.id)
    hoy_date = date.today()
    r = client.post(
        f"/config/estaciones/{estacion.id}/recomputar-eventos/",
        json={"fecha_desde": hoy_date.isoformat(), "fecha_hasta": hoy_date.isoformat()},
    )
    assert r.status_code == 200

    registros = db.exec(
        select(RegistroAuditoria).where(
            RegistroAuditoria.tenant_id == tenant_a,
            RegistroAuditoria.entidad == "recomputo_eventos",
        )
    ).all()
    assert len(registros) == 1
    registro = registros[0]
    assert registro.entidad_id == str(estacion.id)
    assert registro.accion == "ejecutar"
    assert registro.usuario_id == gerente_a.id
    assert registro.detalle is not None
    assert "recomputados=" in registro.detalle
    assert registro.creado_at is not None


def test_recomputo_no_persiste_auditoria_si_falla_la_validacion(client, db, tenant_a, gerente_a):
    """fecha_hasta < fecha_desde rechaza con 400 antes de tocar nada --
    no debe quedar ningún registro de auditoría de un recómputo que
    nunca corrió."""
    planta, linea, estacion = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)

    r = client.post(
        f"/config/estaciones/{estacion.id}/recomputar-eventos/",
        json={"fecha_desde": "2026-08-10", "fecha_hasta": "2026-08-01"},
    )
    assert r.status_code == 400

    registros = db.exec(
        select(RegistroAuditoria).where(RegistroAuditoria.tenant_id == tenant_a)
    ).all()
    assert registros == []


def test_recomputo_de_otro_tenant_no_se_mezcla_en_auditoria(client, db, tenant_a, tenant_b, gerente_a, gerente_b):
    planta_a, linea_a, estacion_a = _preparar_escenario(db, tenant_a)
    planta_b, linea_b, estacion_b = _preparar_escenario(db, tenant_b)

    for gerente, estacion in ((gerente_a, estacion_a), (gerente_b, estacion_b)):
        autenticar_como(gerente.id)
        hoy_date = date.today()
        r = client.post(
            f"/config/estaciones/{estacion.id}/recomputar-eventos/",
            json={"fecha_desde": hoy_date.isoformat(), "fecha_hasta": hoy_date.isoformat()},
        )
        assert r.status_code == 200

    registros_a = db.exec(
        select(RegistroAuditoria).where(RegistroAuditoria.tenant_id == tenant_a)
    ).all()
    assert len(registros_a) == 1
    assert registros_a[0].usuario_id == gerente_a.id

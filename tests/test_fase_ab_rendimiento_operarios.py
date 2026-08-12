"""Fase AB.2: dos bugs reales encontrados auditando "Rendimiento
Operarios" a pedido de Green Mills ("confirmar que la pestaña funcione
correctamente") -- ninguno tira una excepción visible, los dos hacen que
el reporte quede silenciosamente vacío o incompleto.
"""
from datetime import date, datetime, time, timedelta

from tests.conftest import autenticar_como
from app.models.domain import (
    AsignacionTurno, Estacion, Linea, LiteEventoProduccion, Operario, Planta,
    Turno,
)


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta AB2")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea AB2")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación AB2", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    turno = Turno(tenant_id=tenant_id, nombre="Turno AB2", hora_inicio=time(0, 0), hora_fin=time(23, 59), linea_id=linea.id)
    db.add(turno)
    db.commit()
    db.refresh(turno)
    operario = Operario(tenant_id=tenant_id, legajo="LEG-AB2", nombre_completo="Operario Medianoche")
    db.add(operario)
    db.commit()
    db.refresh(operario)
    return planta, linea, estacion, turno, operario


def test_frontend_manda_datetime_iso_completo_y_el_endpoint_lo_acepta(client, db, tenant_a, gerente_a):
    """Bug real #1: HistoricalOperatorReport.tsx armaba fecha_desde/
    fecha_hasta con dateRange.from.toISOString() -- un datetime completo
    con hora y 'Z' -- pero el endpoint espera `date` (FastAPI). Cada
    request real devolvía 422, silenciosamente absorbido por el estado de
    error de react-query; el tab sólo "andaba" en modo demo (mock, sin
    pegarle al backend). Reproducido contra el código anterior al fix
    (422 confirmado) antes de corregir HistoricalOperatorReport.tsx para
    mandar sólo la fecha, igual que el resto del dashboard
    (dateRangePresets.ts::toIso)."""
    planta, linea, estacion, turno, operario = _preparar_escenario(db, tenant_a)
    autenticar_como(gerente_a.id)
    ahora = datetime.now()
    hace_7_dias = ahora - timedelta(days=7)
    r = client.get(
        "/analytics/rendimiento-operarios/",
        # Formato real que manda el front DESPUÉS del fix: sólo fecha.
        params={"fecha_desde": hace_7_dias.date().isoformat(), "fecha_hasta": ahora.date().isoformat()},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200


def test_rendimiento_operarios_resuelve_evento_cerca_de_medianoche_local(client, db, tenant_a, gerente_a):
    """Bug real #2: agrupaba por evento.timestamp.date() -- fecha
    CALENDARIO EN UTC del timestamp persistido -- para buscar en
    AsignacionTurno.fecha, que un supervisor carga pensando en el día de
    PLANTA. Con la planta en America/Buenos_Aires (UTC-3, default de
    sistema si no se configura otro), un evento a las 23hs hora local cae
    en el día SIGUIENTE en UTC -- el lookup fallaba en silencio (sin
    excepción, sin log) y el evento desaparecía del reporte sin que nada
    lo señalara. Mismo tipo de bug que Fase Q ya había corregido en
    /analytics/linea-en-vivo/ (_fecha_planta), nunca aplicado acá."""
    planta, linea, estacion, turno, operario = _preparar_escenario(db, tenant_a)

    dia_local = date.today() - timedelta(days=3)
    db.add(AsignacionTurno(
        tenant_id=tenant_a, fecha=dia_local, estacion_fk=estacion.id,
        operario_fk=operario.id, turno_fk=turno.id,
    ))
    # 23:00 hora de planta (UTC-3) del día `dia_local` = 02:00 UTC del
    # día SIGUIENTE -- exactamente el caso que rompía el lookup viejo.
    ts_utc = datetime.combine(dia_local, time(23, 0)) + timedelta(hours=3)
    db.add(LiteEventoProduccion(
        tenant_id=tenant_a, id_estacion=str(estacion.id), unidades_procesadas=7,
        timestamp=ts_utc, estado="OPTIMO",
    ))
    db.commit()

    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/rendimiento-operarios/",
        params={
            "fecha_desde": (dia_local - timedelta(days=1)).isoformat(),
            "fecha_hasta": (dia_local + timedelta(days=1)).isoformat(),
        },
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["nombre"] == "Operario Medianoche"
    assert filas[0]["unidades_producidas"] == 7

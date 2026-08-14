"""Fase CK (diferenciadores P2, auditoría de UX, batch 3): drill-down
pérdida→evento→estación→responsable. Cubre el último eslabón que
faltaba -- quién clasificó cada parada -- expuesto en /supervisor/paradas
(para el drill-down en sí) y en /analytics/pareto-paradas/ (motivo_fk,
para que el front pueda pedir el detalle de un motivo exacto sin
re-matchear por nombre)."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.domain import (
    Estacion, EstadoParada, Linea, MotivoParada, Planta,
    ParadaDetectada, RolUsuario, TipoParada, UsuarioPlanta,
)
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta CK")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(
        tenant_id=tenant_id, planta_id=planta.id, nombre="Línea CK",
        tiempo_ideal_seg=100, tiempo_lento_seg=150, tiempo_alerta_seg=200,
    )
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación CK", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    motivo = MotivoParada(tenant_id=tenant_id, nombre="Rotura de hilo", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    return planta, linea, estacion, motivo


def _usuario_con_planta(db, tenant_id, rol, planta_id):
    usuario = crear_usuario(db, tenant_id, rol)
    db.add(UsuarioPlanta(tenant_id=tenant_id, usuario_id=usuario.id, planta_id=planta_id, activo=True))
    db.commit()
    return usuario


def _crear_parada(db, tenant_id, estacion_id, *, duracion=1000.0):
    parada = ParadaDetectada(
        tenant_id=tenant_id, estacion_fk=estacion_id,
        inicio=datetime.now(timezone.utc).replace(tzinfo=None), duracion_segundos=duracion,
        estado=EstadoParada.PENDIENTE, origen="AUTOMATICA",
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)
    return parada


def test_clasificar_registra_quien_clasifico(client, db, tenant_a):
    planta, _, estacion, motivo = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    supervisor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    autenticar_como(supervisor.id)

    r = client.patch(
        f"/supervisor/paradas/{parada.id}/clasificar",
        json={"motivo_fk": str(motivo.id)},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json()["clasificado_por_id"] == str(supervisor.id)


def test_reclasificar_pisa_el_responsable_anterior(client, db, tenant_a):
    """La última persona que decidió es la responsable vigente -- no se
    acumula un historial de re-clasificaciones, mismo criterio que
    motivo_fk (el campo de al lado, que también se pisa)."""
    planta, _, estacion, motivo = _preparar_escenario(db, tenant_a)
    otro_motivo = MotivoParada(tenant_id=tenant_a, nombre="Falta de tela", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(otro_motivo)
    db.commit()
    db.refresh(otro_motivo)
    parada = _crear_parada(db, tenant_a, estacion.id)
    supervisor1 = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    supervisor2 = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}

    autenticar_como(supervisor1.id)
    client.patch(f"/supervisor/paradas/{parada.id}/clasificar", json={"motivo_fk": str(motivo.id)}, headers=headers)

    autenticar_como(supervisor2.id)
    r = client.patch(f"/supervisor/paradas/{parada.id}/clasificar", json={"motivo_fk": str(otro_motivo.id)}, headers=headers)
    assert r.json()["clasificado_por_id"] == str(supervisor2.id)


def test_historial_expone_nombre_de_quien_clasifico(client, db, tenant_a):
    planta, _, estacion, motivo = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    supervisor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(supervisor.id)
    client.patch(f"/supervisor/paradas/{parada.id}/clasificar", json={"motivo_fk": str(motivo.id)}, headers=headers)

    r = client.get(f"/supervisor/paradas?motivo_fk={motivo.id}", headers=headers)
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["clasificado_por_id"] == str(supervisor.id)
    assert filas[0]["clasificado_por_nombre"] is not None


def test_pareto_expone_motivo_fk_para_drilldown(client, db, tenant_a, gerente_a):
    planta, linea, estacion, motivo = _preparar_escenario(db, tenant_a)
    db.add(UsuarioPlanta(tenant_id=tenant_a, usuario_id=gerente_a.id, planta_id=planta.id, activo=True))
    db.commit()
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(gerente_a.id)

    parada = _crear_parada(db, tenant_a, estacion.id)
    client.patch(f"/supervisor/paradas/{parada.id}/clasificar", json={"motivo_fk": str(motivo.id)}, headers=headers)

    hoy = datetime.now(timezone.utc).date().isoformat()
    r = client.get(f"/analytics/pareto-paradas/?fecha_desde={hoy}&fecha_hasta={hoy}", headers=headers)
    assert r.status_code == 200
    filas = r.json()
    assert len(filas) == 1
    assert filas[0]["motivo"] == "Rotura de hilo"
    assert filas[0]["motivo_fk"] == str(motivo.id)


def test_drilldown_por_motivo_fk_trae_solo_los_eventos_de_ese_motivo(client, db, tenant_a):
    """El caso de uso completo: clic en una barra del Pareto (motivo_fk)
    -> /supervisor/paradas?motivo_fk=X trae exactamente esos eventos, con
    estación y responsable, no los de otro motivo."""
    planta, _, estacion, motivo = _preparar_escenario(db, tenant_a)
    otro_motivo = MotivoParada(tenant_id=tenant_a, nombre="Falta de tela", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(otro_motivo)
    db.commit()
    db.refresh(otro_motivo)
    supervisor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(supervisor.id)

    parada_a = _crear_parada(db, tenant_a, estacion.id, duracion=300)
    parada_b = _crear_parada(db, tenant_a, estacion.id, duracion=500)
    parada_otro = _crear_parada(db, tenant_a, estacion.id, duracion=700)
    client.patch(f"/supervisor/paradas/{parada_a.id}/clasificar", json={"motivo_fk": str(motivo.id)}, headers=headers)
    client.patch(f"/supervisor/paradas/{parada_b.id}/clasificar", json={"motivo_fk": str(motivo.id)}, headers=headers)
    client.patch(f"/supervisor/paradas/{parada_otro.id}/clasificar", json={"motivo_fk": str(otro_motivo.id)}, headers=headers)

    r = client.get(f"/supervisor/paradas?motivo_fk={motivo.id}", headers=headers)
    assert r.status_code == 200
    ids = {f["id"] for f in r.json()}
    assert ids == {str(parada_a.id), str(parada_b.id)}
    for fila in r.json():
        assert fila["estacion_nombre"] == "Estación CK"
        assert fila["clasificado_por_nombre"] is not None

"""Fase CQ (auditoría de frontend, P0-07): "Deshacer" clasificación --
la parada vuelve a PENDIENTE, motivo_fk/clasificado_por_id se limpian.
Soporte backend para el snackbar de Deshacer del modo planta."""
import uuid
from datetime import datetime, timezone

from app.models.domain import (
    Estacion, EstadoParada, Linea, MotivoParada, Planta,
    ParadaDetectada, RolUsuario, TipoParada, UsuarioPlanta,
)
from tests.conftest import autenticar_como, crear_usuario


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta CQ")
    db.add(planta)
    db.commit()
    db.refresh(planta)
    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea CQ")
    db.add(linea)
    db.commit()
    db.refresh(linea)
    estacion = Estacion(tenant_id=tenant_id, nombre="Estación CQ", tipo="sensor", linea_id=linea.id, activa=True)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    motivo = MotivoParada(tenant_id=tenant_id, nombre="Falta de material", tipo_parada=TipoParada.NO_PLANIFICADA)
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    return planta, estacion, motivo


def _usuario_con_planta(db, tenant_id, rol, planta_id):
    usuario = crear_usuario(db, tenant_id, rol)
    db.add(UsuarioPlanta(tenant_id=tenant_id, usuario_id=usuario.id, planta_id=planta_id, activo=True))
    db.commit()
    return usuario


def _crear_parada(db, tenant_id, estacion_id):
    parada = ParadaDetectada(
        tenant_id=tenant_id, estacion_fk=estacion_id,
        inicio=datetime.now(timezone.utc).replace(tzinfo=None), duracion_segundos=200,
        estado=EstadoParada.PENDIENTE, origen="AUTOMATICA",
    )
    db.add(parada)
    db.commit()
    db.refresh(parada)
    return parada


def test_desclasificar_vuelve_a_pendiente_y_limpia_motivo_y_responsable(client, db, tenant_a):
    planta, estacion, motivo = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)
    supervisor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(supervisor.id)

    client.patch(f"/supervisor/paradas/{parada.id}/clasificar", json={"motivo_fk": str(motivo.id)}, headers=headers)

    r = client.patch(f"/supervisor/paradas/{parada.id}/desclasificar", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["estado"] == "pendiente"
    assert body["motivo_fk"] is None
    assert body["clasificado_por_id"] is None

    # Vuelve a aparecer en la cola de pendientes.
    r_pendientes = client.get("/supervisor/paradas-pendientes", headers=headers)
    assert str(parada.id) in [p["id"] for p in r_pendientes.json()]


def test_no_se_puede_desclasificar_una_parada_ya_pendiente(client, db, tenant_a):
    planta, estacion, _ = _preparar_escenario(db, tenant_a)
    parada = _crear_parada(db, tenant_a, estacion.id)  # nunca clasificada
    supervisor = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta.id)
    headers = {"X-Sub-Tenant-Id": str(planta.id)}
    autenticar_como(supervisor.id)

    r = client.patch(f"/supervisor/paradas/{parada.id}/desclasificar", headers=headers)
    assert r.status_code == 409


def test_no_se_puede_desclasificar_parada_de_otro_tenant(client, db, tenant_a, tenant_b):
    planta_a, estacion_a, motivo_a = _preparar_escenario(db, tenant_a)
    parada_a = _crear_parada(db, tenant_a, estacion_a.id)
    supervisor_a = _usuario_con_planta(db, tenant_a, RolUsuario.SUPERVISOR, planta_a.id)
    headers_a = {"X-Sub-Tenant-Id": str(planta_a.id)}
    autenticar_como(supervisor_a.id)
    client.patch(f"/supervisor/paradas/{parada_a.id}/clasificar", json={"motivo_fk": str(motivo_a.id)}, headers=headers_a)

    supervisor_b = crear_usuario(db, tenant_b, RolUsuario.SUPERVISOR)
    autenticar_como(supervisor_b.id)
    r = client.patch(f"/supervisor/paradas/{parada_a.id}/desclasificar")
    assert r.status_code in (400, 404)

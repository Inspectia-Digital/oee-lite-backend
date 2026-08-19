"""BE-P0-06 (PRD Go-Live Green Mills, auditoría backend 18/8): sesión de
operario inmutable. Antes login_operario_terminal sólo pisaba
operario_fk en la misma fila de AsignacionTurno -- un segundo login (el
caso normal en un cambio de turno) borraba sin dejar rastro quién había
trabajado ahí antes. Los tres criterios de aceptación del PRD, cada uno
con su test:

1. Login de operario B no sobrescribe la sesión de operario A.
2. Evento entre la salida de A y la entrada de B no se atribuye a nadie.
3. KPI por operario reconstruible desde el histórico de sesiones (A y B
   en la misma estación+turno+día, cada uno con SU producción real).
"""
import uuid
from datetime import timedelta

from sqlmodel import select

from app.core.tiempo_planta import fecha_local
from app.models.domain import (
    Estacion, Linea, LiteEventoProduccion, Operario, Planta,
    SesionOperario, Turno,
)
from tests.conftest import autenticar_como


def _preparar_escenario(db, tenant_id):
    planta = Planta(tenant_id=tenant_id, nombre="Planta BE-P0-06")
    db.add(planta)
    db.commit()
    db.refresh(planta)

    linea = Linea(tenant_id=tenant_id, planta_id=planta.id, nombre="Línea BE-P0-06")
    db.add(linea)
    db.commit()
    db.refresh(linea)

    estacion = Estacion(tenant_id=tenant_id, nombre="Estación BE-P0-06", tipo="sensor", linea_id=linea.id)
    db.add(estacion)
    db.commit()
    db.refresh(estacion)

    turno = Turno(tenant_id=tenant_id, nombre="Full", hora_inicio="00:00:00", hora_fin="23:59:00", linea_id=linea.id)
    db.add(turno)
    db.commit()
    db.refresh(turno)

    return planta, linea, estacion, turno


def _crear_operario(db, tenant_id, nombre):
    operario = Operario(tenant_id=tenant_id, legajo=f"LEG-{uuid.uuid4().hex[:8]}", nombre_completo=nombre)
    db.add(operario)
    db.commit()
    db.refresh(operario)
    return operario


def _emitir_credencial(client, gerente, estacion_id):
    autenticar_como(gerente.id)
    r = client.post("/config/api-keys/", json={"estacion_id": str(estacion_id)})
    assert r.status_code == 201
    return r.json()["credencial_completa"]


def _login(client, credencial, legajo, turno_id):
    r = client.post(
        "/api/lite/operario/login",
        json={"legajo": legajo, "turno_fk": str(turno_id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 200, r.text
    return r


def _logout(client, credencial, turno_id):
    r = client.post(
        "/api/lite/operario/logout",
        json={"turno_fk": str(turno_id)},
        headers={"X-Device-Key": credencial},
    )
    assert r.status_code == 200, r.text
    return r


def _sesion_abierta(db, tenant_id, estacion_id, operario_id):
    return db.exec(
        select(SesionOperario).where(
            SesionOperario.tenant_id == tenant_id,
            SesionOperario.estacion_fk == estacion_id,
            SesionOperario.operario_fk == operario_id,
        )
    ).first()


def test_login_de_b_no_sobrescribe_la_sesion_de_a(client, db, tenant_a, gerente_a):
    planta, linea, estacion, turno = _preparar_escenario(db, tenant_a)
    op_a = _crear_operario(db, tenant_a, "Operario A")
    op_b = _crear_operario(db, tenant_a, "Operario B")
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    _login(client, credencial, op_a.legajo, turno.id)
    _login(client, credencial, op_b.legajo, turno.id)  # B releva a A sin que A haya hecho [SALIR]

    sesion_a = _sesion_abierta(db, tenant_a, estacion.id, op_a.id)
    sesion_b = _sesion_abierta(db, tenant_a, estacion.id, op_b.id)

    # La sesión de A sigue existiendo -- login de B la CERRÓ, no la borró
    # ni la pisó (a diferencia de AsignacionTurno, que sí se pisa -- eso
    # es un concepto distinto, dotación planificada, no cambia).
    assert sesion_a is not None
    assert sesion_a.salida is not None
    assert sesion_a.operario_fk == op_a.id  # nunca se reasignó a B

    # La de B está abierta.
    assert sesion_b is not None
    assert sesion_b.salida is None
    assert sesion_b.entrada == sesion_a.salida  # sin hueco: B relevó exactamente cuando A se cerró


def test_evento_entre_salida_de_a_y_entrada_de_b_no_se_atribuye_a_nadie(client, db, tenant_a, gerente_a):
    planta, linea, estacion, turno = _preparar_escenario(db, tenant_a)
    op_a = _crear_operario(db, tenant_a, "Operario A")
    op_b = _crear_operario(db, tenant_a, "Operario B")
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    _login(client, credencial, op_a.legajo, turno.id)
    _logout(client, credencial, turno.id)  # A se va -- hueco real hasta que alguien más loguee
    sesion_a = _sesion_abierta(db, tenant_a, estacion.id, op_a.id)
    assert sesion_a.salida is not None

    _login(client, credencial, op_b.legajo, turno.id)
    sesion_b = _sesion_abierta(db, tenant_a, estacion.id, op_b.id)
    assert sesion_b.entrada > sesion_a.salida  # hueco real entre logout de A y login de B

    # Evento justo en el medio del hueco -- no pertenece a ningún intervalo.
    mitad_del_hueco = sesion_a.salida + (sesion_b.entrada - sesion_a.salida) / 2
    db.add(LiteEventoProduccion(
        tenant_id=tenant_a, id_estacion=str(estacion.id), timestamp=mitad_del_hueco,
        unidades_procesadas=1, estado="OPTIMO",
    ))
    db.commit()

    autenticar_como(gerente_a.id)
    # BE-P0-03 (fase EK): fecha LOCAL de planta, no de calendario UTC
    # (ver comentario homólogo más abajo en este archivo).
    fecha_hueco = fecha_local(mitad_del_hueco, planta).isoformat()
    r = client.get(
        "/analytics/rendimiento-operarios/",
        params={"fecha_desde": fecha_hueco, "fecha_hasta": fecha_hueco},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    assert r.json() == []  # no atribuido a A, no atribuido a B, no atribuido a nadie


def test_kpi_reconstruible_desde_historial_de_sesiones_dos_operarios_mismo_dia(client, db, tenant_a, gerente_a):
    """A y B trabajan la MISMA estación+turno+día, uno relevando al otro
    -- caso que con AsignacionTurno (una sola fila) era literalmente
    imposible de distinguir: todo terminaba atribuido al último que
    logueó. Con SesionOperario, cada uno se queda con SU producción real."""
    planta, linea, estacion, turno = _preparar_escenario(db, tenant_a)
    op_a = _crear_operario(db, tenant_a, "Operario A")
    op_b = _crear_operario(db, tenant_a, "Operario B")
    credencial = _emitir_credencial(client, gerente_a, estacion.id)

    _login(client, credencial, op_a.legajo, turno.id)
    _login(client, credencial, op_b.legajo, turno.id)  # B releva a A casi enseguida (mismo test que arriba)

    db.expire_all()  # sesion_a se cerró recién por el login de B -- releer, no reusar la instancia vieja
    sesion_a = _sesion_abierta(db, tenant_a, estacion.id, op_a.id)
    sesion_b = _sesion_abierta(db, tenant_a, estacion.id, op_b.id)
    assert sesion_a.salida is not None  # cerrada por el login de B

    # 2 eventos DENTRO de la ventana real de A (entrada..salida, angosta
    # porque B logueó casi enseguida -- se usa el punto medio, no un
    # offset fijo como "+5 min" que podría caer después de que B ya
    # cerró la sesión de A).
    ts_de_a = sesion_a.entrada + (sesion_a.salida - sesion_a.entrada) / 2
    for _ in range(2):
        db.add(LiteEventoProduccion(
            tenant_id=tenant_a, id_estacion=str(estacion.id), timestamp=ts_de_a,
            unidades_procesadas=10, estado="OPTIMO",
        ))
    db.commit()

    # 3 eventos mientras B está trabajando (sesión de B sigue abierta).
    for _ in range(3):
        db.add(LiteEventoProduccion(
            tenant_id=tenant_a, id_estacion=str(estacion.id),
            timestamp=sesion_b.entrada + timedelta(minutes=5),
            unidades_procesadas=7, estado="OPTIMO",
        ))
    db.commit()

    # BE-P0-03 (fase EK, bug real encontrado por CI cerca de medianoche
    # UTC): /analytics/rendimiento-operarios/ trata fecha_desde/
    # fecha_hasta como fecha LOCAL de planta -- tomar la fecha de
    # CALENDARIO UTC de `entrada` (naive) puede diferir de la fecha
    # LOCAL de ese mismo instante. `fecha_local` hace la conversión
    # correcta (misma que usa el propio endpoint).
    fecha = fecha_local(sesion_a.entrada, planta).isoformat()
    autenticar_como(gerente_a.id)
    r = client.get(
        "/analytics/rendimiento-operarios/",
        params={"fecha_desde": fecha, "fecha_hasta": fecha},
        headers={"X-Sub-Tenant-Id": str(planta.id)},
    )
    assert r.status_code == 200
    filas = {f["operario_id"]: f for f in r.json()}

    assert str(op_a.id) in filas
    assert str(op_b.id) in filas
    assert filas[str(op_a.id)]["unidades_producidas"] == 20  # 2 eventos x 10 -- nada de B se le mezcla
    assert filas[str(op_b.id)]["unidades_producidas"] == 21  # 3 eventos x 7 -- nada de A se le mezcla

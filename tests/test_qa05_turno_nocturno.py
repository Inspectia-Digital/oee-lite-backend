"""QA-05 (auditoría QA): un turno que cruza medianoche (ej. lunes
22:00-06:00) se resolvía comparando dias_semana contra el día
CALENDARIO del momento consultado, no contra el día en que el turno
ARRANCA -- consultado el martes a las 02:00 (todavía dentro del turno
que empezó el lunes a la noche), dia_iso ya era martes (2), no lunes
(1), así que _dia_en_dias_semana rechazaba el turno antes siquiera de
llegar a evaluar el cruce de medianoche.

Estos son tests unitarios puros de _resolver_turno_por_horario (sin
wall-clock, sin DB) -- exactamente el mecanismo que ahora comparten
/analytics/linea-en-vivo/ y la resolución por timestamp arbitrario
(Fase AD/AN), así que cubre las dos consumidoras con un solo test.
"""
from datetime import time

from app.routers.analytics import _resolver_turno_por_horario
from app.models.domain import Turno


def _turno(hora_inicio, hora_fin, dias_semana, tenant_id="t"):
    return Turno(
        tenant_id=tenant_id, nombre="Turno test",
        hora_inicio=hora_inicio, hora_fin=hora_fin, dias_semana=dias_semana,
    )


def test_turno_nocturno_resuelve_en_la_madrugada_del_dia_siguiente():
    """Turno lunes(1) 22:00-06:00, consultado MARTES(2) a las 02:00 --
    debe resolver igual: sigue siendo el turno que arrancó el lunes."""
    turno = _turno(time(22, 0), time(6, 0), "1")
    resultado = _resolver_turno_por_horario(time(2, 0), 2, [turno])
    assert resultado is not None
    assert resultado.dias_semana == "1"


def test_turno_nocturno_resuelve_en_la_noche_del_dia_de_inicio():
    """Mismo turno, consultado LUNES(1) a las 23:00 -- la mitad "de
    noche", donde el día de consulta coincide con dias_semana
    directamente (nunca estuvo roto, pero confirma que el fix no lo
    rompió)."""
    turno = _turno(time(22, 0), time(6, 0), "1")
    resultado = _resolver_turno_por_horario(time(23, 0), 1, [turno])
    assert resultado is not None


def test_turno_nocturno_no_resuelve_fuera_de_su_franja():
    """A las 10:00 del martes ya no es turno nocturno de ningún tipo --
    ni la franja "de noche" (después de 22:00) ni "de madrugada" (antes
    de 06:00)."""
    turno = _turno(time(22, 0), time(6, 0), "1")
    assert _resolver_turno_por_horario(time(10, 0), 2, [turno]) is None


def test_turno_nocturno_no_resuelve_madrugada_de_dia_no_habilitado():
    """El turno nocturno es domingo(7) 22:00-06:00 (dias_semana="7") --
    consultado el LUNES a las 02:00, el día de arranque real (domingo)
    sigue siendo el correcto a chequear, y domingo SÍ está habilitado,
    así que resuelve. Si en cambio el turno fuera SÓLO viernes(5), un
    lunes a la madrugada (día de arranque = domingo) no debería
    matchear -- confirma que no se acepta de más."""
    turno_domingo = _turno(time(22, 0), time(6, 0), "7")
    assert _resolver_turno_por_horario(time(2, 0), 1, [turno_domingo]) is not None

    turno_viernes = _turno(time(22, 0), time(6, 0), "5")
    assert _resolver_turno_por_horario(time(2, 0), 1, [turno_viernes]) is None


def test_turno_diurno_sigue_funcionando_sin_cambios():
    """Regresión: un turno que NO cruza medianoche (hora_inicio <=
    hora_fin) sigue evaluando dias_semana contra el día de consulta tal
    cual, sin ningún ajuste."""
    turno = _turno(time(6, 0), time(14, 0), "1,2,3,4,5")
    assert _resolver_turno_por_horario(time(10, 0), 3, [turno]) is not None  # miércoles, en horario
    assert _resolver_turno_por_horario(time(10, 0), 6, [turno]) is None  # sábado, no habilitado
    assert _resolver_turno_por_horario(time(20, 0), 3, [turno]) is None  # miércoles, fuera de horario


def test_dias_semana_vacio_aplica_todos_los_dias_turno_nocturno():
    """_dia_en_dias_semana ya trataba dias_semana vacío/corrupto como
    "todos los días" -- confirma que sigue así también en la rama
    nocturna del fix."""
    turno = _turno(time(22, 0), time(6, 0), "")
    assert _resolver_turno_por_horario(time(2, 0), 4, [turno]) is not None

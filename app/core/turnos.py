"""Resolución de "qué turno está vigente" -- una sola fuente de verdad.

FE-P0-03 (PRD Go-Live Green Mills): esta lógica vivía sólo dentro de
analytics.py (`_resolver_turno_por_horario`), que es un router de
analítica con dependencias de auth HUMANO. La Terminal necesita la misma
resolución pero autenticada por credencial M2M del dispositivo, así que
se extrae acá -- ni analytics ni scans son "dueños" de la regla.

La regla en sí no cambia respecto de QA-05/Fase Q: se conserva tal cual,
incluidos sus casos borde de turno nocturno.
"""
from datetime import time
from typing import List, Optional, Protocol


class TurnoLike(Protocol):
    """Lo mínimo que esta resolución necesita de un Turno -- así la
    función es testeable sin construir el modelo entero."""
    hora_inicio: time
    hora_fin: time
    dias_semana: str


def dia_iso_anterior(dia_iso: int) -> int:
    """1=lunes..7=domingo (Fase Q) -- el día anterior a lunes es domingo."""
    return 7 if dia_iso == 1 else dia_iso - 1


def dia_en_dias_semana(dia_iso: int, dias_semana: str) -> bool:
    """dias_semana: CSV de días ISO (1=lunes..7=domingo, Fase Q). Un valor
    corrupto/vacío no bloquea -- mejor de más (aplica todos los días) que
    hacer desaparecer un turno/regla entero por un dato mal cargado."""
    try:
        dias = {int(d) for d in dias_semana.split(",") if d.strip()}
    except (ValueError, AttributeError):
        return True
    if not dias:
        return True
    return dia_iso in dias


def resolver_turno_vigente(hora_local: time, dia_iso: int, turnos: List[TurnoLike]) -> Optional[TurnoLike]:
    """Qué Turno (de una lista ya acotada a una línea) está vigente para
    una hora+día puntuales -- horario + día de semana, primer match gana.
    Usada tanto con la hora/día de AHORA (/analytics/linea-en-vivo/,
    /api/lite/turno-vigente) como para un timestamp arbitrario (Fase AD/AN).

    QA-05 (auditoría QA): antes se chequeaba dias_semana contra el día
    CALENDARIO del momento consultado, sin importar si el turno cruza
    medianoche -- un turno "lunes 22:00-06:00" (dias_semana="1")
    consultado el MARTES a las 02:00 nunca resolvía, porque dia_iso ya
    era martes (2), no lunes (1), aunque el turno siga técnicamente en
    curso (arrancó el lunes a la noche). dias_semana describe el día en
    que el turno ARRANCA, no el día calendario de cada instante dentro
    de él -- así que primero se determina si hora_local cae en la mitad
    "de noche" (después de hora_inicio, arrancó HOY) o en la mitad
    "de madrugada" (antes de hora_fin, arrancó AYER) de un turno que
    cruza medianoche, y recién ahí se compara dias_semana contra el día
    que corresponde. None si ninguno matchea -- no se inventa un turno."""
    for t in turnos:
        if t.hora_inicio <= t.hora_fin:
            en_turno = t.hora_inicio <= hora_local <= t.hora_fin
            dia_de_inicio = dia_iso
        elif hora_local >= t.hora_inicio:
            en_turno = True
            dia_de_inicio = dia_iso
        elif hora_local <= t.hora_fin:
            en_turno = True
            dia_de_inicio = dia_iso_anterior(dia_iso)
        else:
            en_turno = False
            dia_de_inicio = dia_iso
        if en_turno and dia_en_dias_semana(dia_de_inicio, t.dias_semana):
            return t
    return None

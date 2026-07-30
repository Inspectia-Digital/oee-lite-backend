"""k_check_constraints_criticos

Fase K (auditoría QA #6, parcial): las invariantes que protegen el
cálculo de OEE -- el corazón del producto -- vivían sólo en Pydantic
(scans.py) o en comentarios, no en Postgres. Un script, una migración
futura o un import mal armado podía persistir un estado imposible sin que
la base lo impidiera. Alcance deliberadamente acotado (criterio Green
Mills primero): sólo no-negatividad de las columnas que entran directo al
cálculo de OEE y la unicidad de la agenda de dotación diaria. Se deja
afuera a propósito: umbral_optimo<=umbral_lento<=umbral_alerta (podría
haber configuraciones legítimas con umbrales iguales) y la coherencia de
tenant_id entre tablas relacionadas (requiere triggers, mayor esfuerzo,
menor relevancia con un solo tenant hoy).

Revision ID: 7af24f2546a7
Revises: d29e1217b5b2
Create Date: 2026-07-30 12:01:35.677633

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7af24f2546a7'
down_revision: Union[str, None] = 'd29e1217b5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # LiteEventoProduccion: entradas directas del cálculo de OEE.
    op.create_check_constraint(
        'ck_lite_eventos_unidades_rechazadas_rango',
        'lite_eventos_produccion',
        'unidades_rechazadas >= 0 AND unidades_rechazadas <= unidades_procesadas',
    )
    op.create_check_constraint(
        'ck_lite_eventos_unidades_procesadas_no_negativo',
        'lite_eventos_produccion',
        'unidades_procesadas >= 0',
    )
    op.create_check_constraint(
        'ck_lite_eventos_cantidad_producida_no_negativo',
        'lite_eventos_produccion',
        'cantidad_producida >= 0',
    )
    op.create_check_constraint(
        'ck_lite_eventos_delta_t_no_negativo',
        'lite_eventos_produccion',
        'delta_t_segundos >= 0',
    )
    op.create_check_constraint(
        'ck_lite_eventos_tiempo_perdido_no_negativo',
        'lite_eventos_produccion',
        'tiempo_perdido_segundos >= 0',
    )
    op.create_check_constraint(
        'ck_lite_eventos_tiempo_ideal_no_negativo',
        'lite_eventos_produccion',
        'tiempo_ideal_seg >= 0',
    )

    # ParadaDetectada: duración nunca negativa.
    op.create_check_constraint(
        'ck_paradas_duracion_no_negativa',
        'paradas_detectadas',
        'duracion_segundos >= 0',
    )

    # Estacion: umbrales de tiempo nunca negativos (no se impone el orden
    # relativo entre ellos -- ver nota arriba).
    op.create_check_constraint(
        'ck_estaciones_umbrales_no_negativos',
        'dim_estaciones',
        'umbral_optimo >= 0 AND umbral_lento >= 0 AND umbral_alerta >= 0',
    )

    # AsignacionTurno (tablero de dotación, Fase H): el POST del endpoint ya
    # hace upsert por (fecha, turno_fk, estacion_fk), pero no había ningún
    # constraint de base que lo garantizara ante una escritura fuera de esa
    # ruta (script, migración futura).
    op.create_unique_constraint(
        'ux_asignaciones_turno_dia',
        'asignaciones_turno',
        ['tenant_id', 'fecha', 'turno_fk', 'estacion_fk'],
    )


def downgrade() -> None:
    op.drop_constraint('ux_asignaciones_turno_dia', 'asignaciones_turno', type_='unique')
    op.drop_constraint('ck_estaciones_umbrales_no_negativos', 'dim_estaciones', type_='check')
    op.drop_constraint('ck_paradas_duracion_no_negativa', 'paradas_detectadas', type_='check')
    op.drop_constraint('ck_lite_eventos_tiempo_ideal_no_negativo', 'lite_eventos_produccion', type_='check')
    op.drop_constraint('ck_lite_eventos_tiempo_perdido_no_negativo', 'lite_eventos_produccion', type_='check')
    op.drop_constraint('ck_lite_eventos_delta_t_no_negativo', 'lite_eventos_produccion', type_='check')
    op.drop_constraint('ck_lite_eventos_cantidad_producida_no_negativo', 'lite_eventos_produccion', type_='check')
    op.drop_constraint('ck_lite_eventos_unidades_procesadas_no_negativo', 'lite_eventos_produccion', type_='check')
    op.drop_constraint('ck_lite_eventos_unidades_rechazadas_rango', 'lite_eventos_produccion', type_='check')

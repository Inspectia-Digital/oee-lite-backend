"""av_plan_motivo_cancelacion

Fase AV (auditoría de frontend, decisión confirmada #2 y FE-P0-04):
PlanProduccion gana `motivo_cancelacion` (nullable) -- DELETE
/config/planes/{id} (desactivar_plan en configuracion.py) podía cancelar
un plan EN_PROGRESO sin dejar ningún rastro de POR QUÉ se canceló a
mitad de camino (a diferencia de una parada, que siempre lleva un
motivo). Nullable porque archivar un plan BORRADOR/PROGRAMADO que nunca
arrancó no exige motivo (ver decisión del plan) -- sólo cancelar uno
EN_PROGRESO lo exige, validado en el endpoint, no acá.

Revision ID: c3a9f5b8d1e4
Revises: 67f069bae880
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a9f5b8d1e4'
down_revision: Union[str, None] = '67f069bae880'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('planes_produccion', sa.Column('motivo_cancelacion', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('planes_produccion', 'motivo_cancelacion')

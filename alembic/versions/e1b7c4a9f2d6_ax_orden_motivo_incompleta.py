"""ax_orden_motivo_incompleta

Fase AX (auditoría de frontend, decisión confirmada #2 y FE-P0-06):
OrdenProduccion gana `motivo_incompleta` (nullable) -- avanzar_orden
(operacion.py) podía cerrar la orden activa con menos unidades reales que
las esperadas sin dejar ningún rastro de por qué (falla de máquina, corte
de turno, etc). Nullable: sólo se completa cuando la orden se cierra
incompleta -- si se completó o sobreproducida no hay nada que explicar.

Revision ID: e1b7c4a9f2d6
Revises: c3a9f5b8d1e4
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1b7c4a9f2d6'
down_revision: Union[str, None] = 'c3a9f5b8d1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ordenes_produccion', sa.Column('motivo_incompleta', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('ordenes_produccion', 'motivo_incompleta')

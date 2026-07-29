"""d1_activo_usuario_planta

Fase D.1 del hardening production-grade. Agrega 'activo' a usuario_planta
(consistencia con MaquinaEstacion, que ya lo tenía) para poder dar de baja
una asignación usuario-planta sin hard-delete. También agrega un índice
único parcial para no duplicar asignaciones activas del mismo usuario a
la misma planta.

El autogenerate de Alembic detectó además varios falsos positivos
(nullable=True en columnas enum y "removed index" sobre los índices
parciales de C1) que no reflejan un cambio real de modelo — ya nos había
pasado en C1 con el mismo patrón. Se descartaron a mano, sólo queda el
cambio real.

Revision ID: c941d511ae50
Revises: d40754a1ed06
Create Date: 2026-07-29 11:14:14.123550

"""
import sqlalchemy as sa
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c941d511ae50'
down_revision: Union[str, None] = 'd40754a1ed06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'usuario_planta',
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(
        'ux_usuario_planta_activo',
        'usuario_planta', ['tenant_id', 'usuario_id', 'planta_id'],
        unique=True, postgresql_where=sa.text('activo'),
    )


def downgrade() -> None:
    op.drop_index('ux_usuario_planta_activo', table_name='usuario_planta')
    op.drop_column('usuario_planta', 'activo')

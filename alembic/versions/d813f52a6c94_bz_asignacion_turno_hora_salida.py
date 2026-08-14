"""bz_asignacion_turno_hora_salida

Fase BZ (auditoría de robustez, batch 3): escanear [SALIR] en la
terminal formaliza el fin de sesión -- antes sólo reseteaba estado local
del front, ningún registro quedaba del lado del backend (ver
POST /api/lite/operario/logout, scans.py). `hora_salida` es puramente
informativo: NO cambia cómo se resuelve el operario de un evento
histórico, eso sigue siendo por (tenant, estación, turno, fecha).

Revision ID: d813f52a6c94
Revises: c47a92e6d0b1
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd813f52a6c94'
down_revision: Union[str, None] = 'c47a92e6d0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('asignaciones_turno', sa.Column('hora_salida', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('asignaciones_turno', 'hora_salida')

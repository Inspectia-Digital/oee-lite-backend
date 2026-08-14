"""ck_clasificado_por_paradas

Fase CK (diferenciadores P2, batch 3): drill-down pérdida→evento→
estación→responsable. Faltaba el último eslabón -- quién clasificó la
parada -- para que el drill-down desde el Pareto de pérdidas del
dashboard pueda mostrar el responsable de cada evento, no sólo el
motivo. NULL para toda parada clasificada antes de esta fase (no se
backfillea con una suposición), mismo criterio que Fase CC.

Revision ID: b3e6a1c8d247
Revises: f4a2d891c6e7
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b3e6a1c8d247'
down_revision: Union[str, None] = 'f4a2d891c6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('paradas_detectadas', sa.Column('clasificado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.create_foreign_key(
        'fk_paradas_detectadas_clasificado_por_id_usuarios_saas',
        'paradas_detectadas', 'usuarios_saas',
        ['clasificado_por_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_paradas_detectadas_clasificado_por_id_usuarios_saas',
        'paradas_detectadas', type_='foreignkey',
    )
    op.drop_column('paradas_detectadas', 'clasificado_por_id')

"""fase_fa3_caracteristicas_plan

Fase FA.3 (PRD Demo/Partners/Marketplace/Soporte/Planes): submódulos/
features por plan -- caracteristicas_modulo (checklist de features por
módulo) + plan_caracteristicas (M2M plan<->feature).

Revision ID: e618fe2586df
Revises: 94857609cdd2
Create Date: 2026-08-22 02:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e618fe2586df'
down_revision: Union[str, None] = '94857609cdd2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'caracteristicas_modulo',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('modulo_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('codigo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('nombre', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('descripcion', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['modulo_id'], ['modulos_disponibles.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_caracteristicas_modulo_modulo_codigo', 'caracteristicas_modulo', ['modulo_id', 'codigo'], unique=True)

    op.create_table(
        'plan_caracteristicas',
        sa.Column('plan_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('caracteristica_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['planes_precio.id']),
        sa.ForeignKeyConstraint(['caracteristica_id'], ['caracteristicas_modulo.id']),
        sa.PrimaryKeyConstraint('plan_id', 'caracteristica_id'),
    )


def downgrade() -> None:
    op.drop_table('plan_caracteristicas')
    op.drop_index('ix_caracteristicas_modulo_modulo_codigo', table_name='caracteristicas_modulo')
    op.drop_table('caracteristicas_modulo')

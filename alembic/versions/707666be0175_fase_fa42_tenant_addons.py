"""fase_fa42_tenant_addons

Fase FA.4.2 (PRD Segmentación de Planes §3.1): tenant_addons_contratados
-- una característica/submódulo contratada SUELTA, fuera de cualquier
plan (caso del PRD: un tenant Free que contrata sólo Planificación).

No se crean `submodulos`/`plan_submodulos`: esa jerarquía ya existe
como caracteristicas_modulo + plan_caracteristicas (Fase FA.3), ver el
docstring de CaracteristicaModulo en domain.py.

Revision ID: 707666be0175
Revises: 9233ed06a377
Create Date: 2026-08-22 04:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '707666be0175'
down_revision: Union[str, None] = '9233ed06a377'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_addons_contratados',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('caracteristica_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('precio', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('estado', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='activa'),
        sa.ForeignKeyConstraint(['caracteristica_id'], ['caracteristicas_modulo.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tenant_addons_contratados_tenant_id', 'tenant_addons_contratados', ['tenant_id'], unique=False)
    op.create_index('ix_tenant_addons_unico', 'tenant_addons_contratados', ['tenant_id', 'caracteristica_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_tenant_addons_unico', table_name='tenant_addons_contratados')
    op.drop_index('ix_tenant_addons_contratados_tenant_id', table_name='tenant_addons_contratados')
    op.drop_table('tenant_addons_contratados')

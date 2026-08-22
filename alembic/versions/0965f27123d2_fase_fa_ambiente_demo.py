"""fase_fa_ambiente_demo

Fase FA (PRD Demo/Partners/Marketplace/Soporte/Planes): columnas de
Ambiente Demo en tenants_saas + tabla de credenciales internas del
simulador (ver app/core/demo_simulador.py). Índice compuesto sobre
(es_demo, demo_expira_at) para que el job de limpieza automática sea
barato (corre cada hora, filtra sobre este índice).

Revision ID: 0965f27123d2
Revises: 18398933b20f
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0965f27123d2'
down_revision: Union[str, None] = '18398933b20f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants_saas', sa.Column('es_demo', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('tenants_saas', sa.Column('industria_demo', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('tenants_saas', sa.Column('demo_simulando_desde', sa.DateTime(), nullable=True))
    op.add_column('tenants_saas', sa.Column('demo_expira_at', sa.DateTime(), nullable=True))
    op.add_column('tenants_saas', sa.Column('demo_velocidad', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='normal'))
    op.create_index('ix_tenants_saas_demo_limpieza', 'tenants_saas', ['es_demo', 'demo_expira_at'], unique=False)

    op.create_table(
        'demo_credenciales_simulador',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('estacion_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('credencial_completa', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['estacion_id'], ['dim_estaciones.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('estacion_id'),
    )
    op.create_index('ix_demo_credenciales_simulador_tenant_id', 'demo_credenciales_simulador', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_demo_credenciales_simulador_tenant_id', table_name='demo_credenciales_simulador')
    op.drop_table('demo_credenciales_simulador')
    op.drop_index('ix_tenants_saas_demo_limpieza', table_name='tenants_saas')
    op.drop_column('tenants_saas', 'demo_velocidad')
    op.drop_column('tenants_saas', 'demo_expira_at')
    op.drop_column('tenants_saas', 'demo_simulando_desde')
    op.drop_column('tenants_saas', 'industria_demo')
    op.drop_column('tenants_saas', 'es_demo')

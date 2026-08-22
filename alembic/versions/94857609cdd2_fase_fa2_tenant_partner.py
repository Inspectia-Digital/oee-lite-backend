"""fase_fa2_tenant_partner

Fase FA.2 (PRD Demo/Partners/Marketplace/Soporte/Planes): Tenant tipo
Partner/Canal/Consultor. tenants_saas.categoria (cliente/partner/
interno) + demo_asociado_id (FK a un tenant demo de Fase FA.1) +
tabla global materiales_partner.

Revision ID: 94857609cdd2
Revises: 0965f27123d2
Create Date: 2026-08-22 01:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '94857609cdd2'
down_revision: Union[str, None] = '0965f27123d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants_saas', sa.Column('categoria', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='cliente'))
    op.add_column('tenants_saas', sa.Column('demo_asociado_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_foreign_key('fk_tenants_saas_demo_asociado_id', 'tenants_saas', 'tenants_saas', ['demo_asociado_id'], ['id'])

    op.create_table(
        'materiales_partner',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('titulo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('descripcion', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('url_archivo', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('categoria', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='otro'),
        sa.Column('visible', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('creado_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('materiales_partner')
    op.drop_constraint('fk_tenants_saas_demo_asociado_id', 'tenants_saas', type_='foreignkey')
    op.drop_column('tenants_saas', 'demo_asociado_id')
    op.drop_column('tenants_saas', 'categoria')

"""af_parada_orden_fk

Fase AF (pedido de Green Mills, "análisis de paradas por SKU"):
ParadaDetectada gana orden_fk (nullable, FK a ordenes_produccion.id) --
qué orden estaba activa cuando se detectó la parada, para poder agrupar
/analytics/paradas-por-sku/ por SKU. Sin backfill: las paradas ya
existentes quedan con orden_fk=NULL (no hay forma confiable de
reconstruir retroactivamente qué orden estaba activa en ese momento
exacto) -- el endpoint las agrupa como "Sin SKU asociado", no se
inventa un valor.

Revision ID: 389e8e2190e8
Revises: 72e94de52591
Create Date: 2026-08-12 19:57:29.064894

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '389e8e2190e8'
down_revision: Union[str, None] = '72e94de52591'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('paradas_detectadas', sa.Column('orden_fk', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.create_foreign_key(
        'fk_paradas_detectadas_orden_fk', 'paradas_detectadas', 'ordenes_produccion',
        ['orden_fk'], ['id'],
    )
    op.create_index('ix_paradas_detectadas_orden_fk', 'paradas_detectadas', ['orden_fk'])


def downgrade() -> None:
    op.drop_index('ix_paradas_detectadas_orden_fk', table_name='paradas_detectadas')
    op.drop_constraint('fk_paradas_detectadas_orden_fk', 'paradas_detectadas', type_='foreignkey')
    op.drop_column('paradas_detectadas', 'orden_fk')

"""r_tolerancia_heredable_tiempo_sku_estacion

Fase R (feedback de producto sobre la app en uso):

- dim_lineas / dim_estaciones: nuevos tolerancia_lento_pct/alerta_pct
  (Optional[float], heredable Estación -> Línea -> Tenant, mismo patrón
  que umbral_optimo/lento/alerta de Fase Q). Antes la tolerancia %
  aplicada cuando un evento resuelve un SKU (scans.py) era un único
  valor fijo por tenant (tenants_saas.tolerancia_lento_pct/alerta_pct,
  sin tocar) -- ahora una Estación o Línea puede pisarlo.

- sku_tiempo_estacion (tabla nueva): override opcional del tiempo ideal
  de un SKU en una Estación puntual (un mismo SKU puede tardar distinto
  según la estación que lo procesa -- MaestroSKU.tiempo_ciclo_teorico es
  un único valor genérico por SKU, no alcanza para eso). Si no hay fila
  para el par (SKU, Estación), scans.py cae al tiempo_ciclo_teorico
  genérico del SKU -- no bloquea nada.

Revision ID: 4bb8874d2726
Revises: f67b61f36bcf
Create Date: 2026-08-10 12:58:10.289202

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '4bb8874d2726'
down_revision: Union[str, None] = 'f67b61f36bcf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Tolerancia % heredable (Línea/Estación) ---
    op.add_column('dim_lineas', sa.Column('tolerancia_lento_pct', sa.Float(), nullable=True))
    op.add_column('dim_lineas', sa.Column('tolerancia_alerta_pct', sa.Float(), nullable=True))
    op.add_column('dim_estaciones', sa.Column('tolerancia_lento_pct', sa.Float(), nullable=True))
    op.add_column('dim_estaciones', sa.Column('tolerancia_alerta_pct', sa.Float(), nullable=True))

    # --- Tiempo ideal por SKU x Estación (override opcional) ---
    op.create_table(
        'sku_tiempo_estacion',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('sku_fk', sa.String(), nullable=False),
        sa.Column('estacion_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('tiempo_ciclo_teorico', sa.Float(), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['sku_fk'], ['maestro_skus.codigo_sku']),
        sa.ForeignKeyConstraint(['estacion_id'], ['dim_estaciones.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sku_tiempo_estacion_tenant_id', 'sku_tiempo_estacion', ['tenant_id'])
    op.create_index(
        'ix_sku_tiempo_estacion_unico', 'sku_tiempo_estacion',
        ['tenant_id', 'sku_fk', 'estacion_id'], unique=True,
    )
    # Aislamiento por tenant: igual que el resto de las tablas de este
    # proyecto, se aplica a nivel de query en cada router
    # (WHERE tenant_id == context.tenant_id), no con RLS de Postgres --
    # ninguna otra migración de este repo usa RLS, no hay que introducirlo
    # sólo para esta tabla.


def downgrade() -> None:
    op.drop_index('ix_sku_tiempo_estacion_unico', table_name='sku_tiempo_estacion')
    op.drop_index('ix_sku_tiempo_estacion_tenant_id', table_name='sku_tiempo_estacion')
    op.drop_table('sku_tiempo_estacion')

    op.drop_column('dim_estaciones', 'tolerancia_alerta_pct')
    op.drop_column('dim_estaciones', 'tolerancia_lento_pct')
    op.drop_column('dim_lineas', 'tolerancia_alerta_pct')
    op.drop_column('dim_lineas', 'tolerancia_lento_pct')

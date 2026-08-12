"""aa_plan_produccion

Fase AA (pedido de Green Mills, reunión de producción): nuevo nivel
"Plan" arriba de Orden -- agrupa las órdenes que se van a producir en un
día. Ver PlanProduccion en app/models/domain.py para el razonamiento
completo (por qué sin fecha_fin, por qué orden_activa_fk es
autoritativo, por qué es opcional por línea).

- planes_produccion (tabla nueva): linea_id, fecha_inicio, estado
  (abierto/cerrado), orden_activa_fk (apunta a ordenes_produccion.id,
  la identidad UUID -- no a id_orden, mismo criterio C1/C2 que el resto
  del esquema).
- ordenes_produccion: nuevos plan_id (nullable -- None = orden suelta,
  comportamiento histórico sin cambios) y secuencia (default 0, orden
  dentro del plan).

Revision ID: 58d82d942308
Revises: 4bb8874d2726
Create Date: 2026-08-12 14:35:37.561506

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '58d82d942308'
down_revision: Union[str, None] = '4bb8874d2726'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'planes_produccion',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('linea_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('estado', sa.String(), nullable=False, server_default='abierto'),
        sa.Column('orden_activa_fk', sqlmodel.sql.sqltypes.GUID(), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['linea_id'], ['dim_lineas.id']),
        sa.ForeignKeyConstraint(['orden_activa_fk'], ['ordenes_produccion.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_planes_produccion_tenant_id', 'planes_produccion', ['tenant_id'])
    op.create_index('ix_planes_produccion_linea_id', 'planes_produccion', ['linea_id'])
    # Aislamiento por tenant a nivel de query en cada router (WHERE
    # tenant_id == context.tenant_id), mismo criterio que el resto del
    # esquema -- ver nota equivalente en la migración de Fase R.

    op.add_column('ordenes_produccion', sa.Column('plan_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('ordenes_produccion', sa.Column('secuencia', sa.Integer(), nullable=False, server_default='0'))
    op.create_foreign_key(
        'fk_ordenes_produccion_plan_id', 'ordenes_produccion', 'planes_produccion',
        ['plan_id'], ['id'],
    )
    op.create_index('ix_ordenes_produccion_plan_id', 'ordenes_produccion', ['plan_id'])


def downgrade() -> None:
    op.drop_index('ix_ordenes_produccion_plan_id', table_name='ordenes_produccion')
    op.drop_constraint('fk_ordenes_produccion_plan_id', 'ordenes_produccion', type_='foreignkey')
    op.drop_column('ordenes_produccion', 'secuencia')
    op.drop_column('ordenes_produccion', 'plan_id')

    op.drop_index('ix_planes_produccion_linea_id', table_name='planes_produccion')
    op.drop_index('ix_planes_produccion_tenant_id', table_name='planes_produccion')
    op.drop_table('planes_produccion')

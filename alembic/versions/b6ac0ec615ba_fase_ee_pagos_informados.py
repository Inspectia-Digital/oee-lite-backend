"""fase_ee_pagos_informados

Revision ID: b6ac0ec615ba
Revises: 7f0465f1c2a7
Create Date: 2026-08-18 03:06:41.605324

Fase EE (PRD "Billing MVP" v2.0): pagos_informados -- el autoinforme del
cliente ("informé este pago") + aprobación/rechazo por SuperAdmin. Mismo
ruido de drift preexistente no relacionado que las migraciones de Fase
EB/EC/ED -- recortado a mano para dejar sólo la tabla nueva de esta fase.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'b6ac0ec615ba'
down_revision: Union[str, None] = '7f0465f1c2a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('pagos_informados',
    sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('factura_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('fecha_pago', sa.Date(), nullable=False),
    sa.Column('monto', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('referencia', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('comprobante_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('estado', sa.Enum('pendiente_revision', 'aprobado', 'rechazado', name='estadopagoinformado'), nullable=True),
    sa.Column('aprobado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('fecha_aprobacion', sa.DateTime(), nullable=True),
    sa.Column('observaciones', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('creado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('creado_at', sa.DateTime(), nullable=False),
    sa.Column('actualizado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('actualizado_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['actualizado_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['aprobado_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['factura_id'], ['facturas.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants_saas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pagos_informados_factura_id'), 'pagos_informados', ['factura_id'], unique=False)
    op.create_index(op.f('ix_pagos_informados_tenant_id'), 'pagos_informados', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pagos_informados_tenant_id'), table_name='pagos_informados')
    op.drop_index(op.f('ix_pagos_informados_factura_id'), table_name='pagos_informados')
    op.drop_table('pagos_informados')

    # Postgres no dropea automáticamente los tipos ENUM nombrados al hacer
    # drop_table -- mismo bug ya encontrado y corregido en Fase EB/EC/ED.
    sa.Enum(name='estadopagoinformado').drop(op.get_bind(), checkfirst=True)

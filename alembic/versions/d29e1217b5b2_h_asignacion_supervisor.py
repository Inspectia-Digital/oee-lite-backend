"""h_asignacion_supervisor

Fase H del hardening (InspectIA OS): tablero de supervisión diaria.
El turno es una plantilla maestra; el supervisor a cargo se registra por
día. Único por (tenant_id, fecha, linea_id, turno_id) -- sin partial index
por 'activo' porque esta tabla no usa baja lógica: reasignar sobrescribe
(upsert), no hay historial de asignaciones "desactivadas" que preservar.

Mismos falsos positivos de autogenerate de siempre, descartados a mano.

Revision ID: d29e1217b5b2
Revises: d0d1aa40e069
Create Date: 2026-07-30 10:14:52.486937

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'd29e1217b5b2'
down_revision: Union[str, None] = 'd0d1aa40e069'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'asignaciones_supervisor',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('linea_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('turno_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('supervisor_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.ForeignKeyConstraint(['linea_id'], ['dim_lineas.id']),
        sa.ForeignKeyConstraint(['supervisor_id'], ['dim_supervisores.id']),
        sa.ForeignKeyConstraint(['turno_id'], ['dim_turnos.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_asignaciones_supervisor_tenant_id'), 'asignaciones_supervisor', ['tenant_id'], unique=False)
    op.create_index(
        'ux_asignacion_supervisor_dia',
        'asignaciones_supervisor', ['tenant_id', 'fecha', 'linea_id', 'turno_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('ux_asignacion_supervisor_dia', table_name='asignaciones_supervisor')
    op.drop_index(op.f('ix_asignaciones_supervisor_tenant_id'), table_name='asignaciones_supervisor')
    op.drop_table('asignaciones_supervisor')

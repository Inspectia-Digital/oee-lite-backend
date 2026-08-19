"""fase_en_registros_rechazo_manual

PRD_HALLAZGOS_REVISION_DIRECTA.md, hallazgo #1: carga manual de unidades
rechazadas para líneas con metodo_calidad="por_rechazo" sin scanner en la
estación de calidad (confirmado con el usuario: la línea de Green Mills
la necesita). Ver docstring de RegistroRechazoManual en domain.py.

Revision ID: d3f7a9c1e4b6
Revises: a1c3e8f56b02
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'd3f7a9c1e4b6'
down_revision: Union[str, None] = 'a1c3e8f56b02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'registros_rechazo_manual',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('orden_fk', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('estacion_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('cantidad_rechazada', sa.Integer(), nullable=False),
        sa.Column('motivo', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('registrado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('creado_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['estacion_id'], ['dim_estaciones.id']),
        sa.ForeignKeyConstraint(['registrado_por_id'], ['usuarios_saas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_registros_rechazo_manual_tenant_id'), 'registros_rechazo_manual', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_registros_rechazo_manual_orden_fk'), 'registros_rechazo_manual', ['orden_fk'], unique=False)
    op.create_index(op.f('ix_registros_rechazo_manual_estacion_id'), 'registros_rechazo_manual', ['estacion_id'], unique=False)
    op.create_index(op.f('ix_registros_rechazo_manual_timestamp'), 'registros_rechazo_manual', ['timestamp'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_registros_rechazo_manual_timestamp'), table_name='registros_rechazo_manual')
    op.drop_index(op.f('ix_registros_rechazo_manual_estacion_id'), table_name='registros_rechazo_manual')
    op.drop_index(op.f('ix_registros_rechazo_manual_orden_fk'), table_name='registros_rechazo_manual')
    op.drop_index(op.f('ix_registros_rechazo_manual_tenant_id'), table_name='registros_rechazo_manual')
    op.drop_table('registros_rechazo_manual')

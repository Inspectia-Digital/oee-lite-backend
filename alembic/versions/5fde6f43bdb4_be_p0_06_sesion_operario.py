"""be_p0_06_sesion_operario

PRD_GO_LIVE_GREEN_MILLS_CONSOLIDADO.md, seccion 3, BE-P0-06: sesion de
operario INMUTABLE -- ver docstring de SesionOperario en domain.py.
AsignacionTurno sigue existiendo tal cual (tablero de dotacion/staffing
planificado, concepto distinto). Trimmed a mano del autogenerate: el
resto era drift preexistente entre el modelo y la base (constraints/
indices/nullability de otras tablas, sin relacion con este cambio),
mismo criterio que el resto de migraciones de esta sesion.

Revision ID: 5fde6f43bdb4
Revises: b6ac0ec615ba
Create Date: 2026-08-18 15:39:10.856062

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '5fde6f43bdb4'
down_revision: Union[str, None] = 'b6ac0ec615ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sesiones_operario',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('operario_fk', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('estacion_fk', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('turno_fk', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('entrada', sa.DateTime(), nullable=False),
        sa.Column('salida', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['estacion_fk'], ['dim_estaciones.id']),
        sa.ForeignKeyConstraint(['operario_fk'], ['dim_operarios.id']),
        sa.ForeignKeyConstraint(['turno_fk'], ['dim_turnos.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sesiones_operario_entrada'), 'sesiones_operario', ['entrada'], unique=False)
    op.create_index(op.f('ix_sesiones_operario_estacion_fk'), 'sesiones_operario', ['estacion_fk'], unique=False)
    op.create_index(op.f('ix_sesiones_operario_operario_fk'), 'sesiones_operario', ['operario_fk'], unique=False)
    op.create_index(op.f('ix_sesiones_operario_salida'), 'sesiones_operario', ['salida'], unique=False)
    op.create_index(op.f('ix_sesiones_operario_tenant_id'), 'sesiones_operario', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sesiones_operario_tenant_id'), table_name='sesiones_operario')
    op.drop_index(op.f('ix_sesiones_operario_salida'), table_name='sesiones_operario')
    op.drop_index(op.f('ix_sesiones_operario_operario_fk'), table_name='sesiones_operario')
    op.drop_index(op.f('ix_sesiones_operario_estacion_fk'), table_name='sesiones_operario')
    op.drop_index(op.f('ix_sesiones_operario_entrada'), table_name='sesiones_operario')
    op.drop_table('sesiones_operario')

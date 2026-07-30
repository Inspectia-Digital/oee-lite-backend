"""f_modulo_permiso

Fase F del hardening (InspectIA OS): matriz de permisos por módulo y planta.
Sólo registra asignaciones explícitas para SUPERVISOR/OPERARIO (mismo criterio
que usuario_planta); SUPERADMIN/GERENCIA/PRODUCCION no necesitan filas acá,
se sintetiza su acceso completo en /accesos/usuarios/me.

El autogenerate volvió a detectar los mismos falsos positivos de siempre
(nullable=True en columnas enum y "removed index" sobre índices parciales ya
existentes de C1/D1) — descartados a mano, sólo queda el cambio real.

Revision ID: d0d1aa40e069
Revises: 3dfb6eb20b53
Create Date: 2026-07-30 09:51:14.519185

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd0d1aa40e069'
down_revision: Union[str, None] = '3dfb6eb20b53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'modulo_permiso',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('usuario_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('modulo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('planta_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('rol', postgresql.ENUM('SUPERADMIN', 'GERENCIA', 'PRODUCCION', 'SUPERVISOR', 'OPERARIO', name='rolusuario', create_type=False), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['planta_id'], ['plantas.id']),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios_saas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_modulo_permiso_modulo'), 'modulo_permiso', ['modulo'], unique=False)
    op.create_index(op.f('ix_modulo_permiso_tenant_id'), 'modulo_permiso', ['tenant_id'], unique=False)
    op.create_index(
        'ux_modulo_permiso_activo',
        'modulo_permiso', ['tenant_id', 'usuario_id', 'modulo', 'planta_id'],
        unique=True, postgresql_where=sa.text('activo'),
    )


def downgrade() -> None:
    op.drop_index('ux_modulo_permiso_activo', table_name='modulo_permiso')
    op.drop_index(op.f('ix_modulo_permiso_tenant_id'), table_name='modulo_permiso')
    op.drop_index(op.f('ix_modulo_permiso_modulo'), table_name='modulo_permiso')
    op.drop_table('modulo_permiso')

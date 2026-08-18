"""fase_du_registro_auditoria

Auditoría de backend P1-02 (parcial): tabla de auditoría genérica --
no existía ningún mecanismo de este tipo en todo el backend (ver
docstring de RegistroAuditoria en domain.py). Primer consumidor:
recomputar_eventos (recomputo.py).

Revision ID: 59a7614d4317
Revises: 6d2f69366db6
Create Date: 2026-08-17 22:09:14.652158

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '59a7614d4317'
down_revision: Union[str, None] = '6d2f69366db6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'registros_auditoria',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('entidad', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('entidad_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('accion', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('usuario_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('detalle', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('creado_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios_saas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_registros_auditoria_tenant_id'), 'registros_auditoria', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_registros_auditoria_entidad'), 'registros_auditoria', ['entidad'], unique=False)
    op.create_index(op.f('ix_registros_auditoria_entidad_id'), 'registros_auditoria', ['entidad_id'], unique=False)
    op.create_index(op.f('ix_registros_auditoria_creado_at'), 'registros_auditoria', ['creado_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_registros_auditoria_creado_at'), table_name='registros_auditoria')
    op.drop_index(op.f('ix_registros_auditoria_entidad_id'), table_name='registros_auditoria')
    op.drop_index(op.f('ix_registros_auditoria_entidad'), table_name='registros_auditoria')
    op.drop_index(op.f('ix_registros_auditoria_tenant_id'), table_name='registros_auditoria')
    op.drop_table('registros_auditoria')

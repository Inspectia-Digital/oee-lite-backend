"""fase_fa41_supervisor_usuario_web

Fase FA.4.1 (bug real, encontrado auditando PRD_SEGMENTACION_PLANES
contra el código): dim_supervisores.usuario_id -- el vínculo entre un
Supervisor y su cuenta web. El frontend ya lo mandaba y ya lo mostraba,
pero la columna no existía: Pydantic descartaba el campo en silencio y
nada se persistía. Nullable a propósito (un supervisor puede estar dado
de alta operativamente sin cuenta web todavía).

Revision ID: 9233ed06a377
Revises: e618fe2586df
Create Date: 2026-08-22 03:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9233ed06a377'
down_revision: Union[str, None] = 'e618fe2586df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('dim_supervisores', sa.Column('usuario_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.create_foreign_key(
        'fk_dim_supervisores_usuario_id_usuarios_saas',
        'dim_supervisores', 'usuarios_saas', ['usuario_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_dim_supervisores_usuario_id_usuarios_saas', 'dim_supervisores', type_='foreignkey')
    op.drop_column('dim_supervisores', 'usuario_id')

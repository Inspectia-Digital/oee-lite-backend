"""cb_api_key_creador_ultimo_uso

Fase CB (auditoría de robustez, batch 3): historial completo de
API-keys de dispositivo -- quién la emitió y cuándo se usó por última
vez. Ambas columnas nullable: las keys emitidas antes de esta fase no
tienen `creado_por_id` (no se puede reconstruir retroactivamente) y
`ultimo_uso_at` arranca en NULL hasta el próximo auth exitoso.

Revision ID: e91f3c7a58d2
Revises: d813f52a6c94
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e91f3c7a58d2'
down_revision: Union[str, None] = 'd813f52a6c94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('api_keys_dispositivo', sa.Column('creado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('api_keys_dispositivo', sa.Column('ultimo_uso_at', sa.DateTime(), nullable=True))
    op.create_foreign_key(
        'fk_api_keys_dispositivo_creado_por_id_usuarios_saas',
        'api_keys_dispositivo', 'usuarios_saas',
        ['creado_por_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_api_keys_dispositivo_creado_por_id_usuarios_saas',
        'api_keys_dispositivo', type_='foreignkey',
    )
    op.drop_column('api_keys_dispositivo', 'ultimo_uso_at')
    op.drop_column('api_keys_dispositivo', 'creado_por_id')

"""cc_exclusion_oee_paradas

Fase CC (FE-P0-08, auditoría de robustez, batch 3): workflow de falso
positivo -- propuesta + aprobación de exclusión de OEE sobre
ParadaDetectada. `exclusion_oee` se guarda como texto plano (mismo
criterio que LiteEventoProduccion.estado, "OPTIMO"/"LENTO"/"ALERTA")
en vez de un ENUM nativo de Postgres -- evita el ALTER TYPE de un enum
ya en uso, sin perder nada: la validación de valores vive en la capa de
aplicación (EstadoExclusionOee, Pydantic/SQLModel), no en la base.

Revision ID: f4a2d891c6e7
Revises: e91f3c7a58d2
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f4a2d891c6e7'
down_revision: Union[str, None] = 'e91f3c7a58d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('paradas_detectadas', sa.Column(
        'exclusion_oee', sqlmodel.sql.sqltypes.AutoString(),
        nullable=False, server_default='ninguna',
    ))
    op.add_column('paradas_detectadas', sa.Column('exclusion_motivo', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('paradas_detectadas', sa.Column('exclusion_propuesta_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('paradas_detectadas', sa.Column('exclusion_propuesta_at', sa.DateTime(), nullable=True))
    op.add_column('paradas_detectadas', sa.Column('exclusion_resuelta_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('paradas_detectadas', sa.Column('exclusion_resuelta_at', sa.DateTime(), nullable=True))
    op.add_column('paradas_detectadas', sa.Column('exclusion_resolucion_nota', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_foreign_key(
        'fk_paradas_detectadas_exclusion_propuesta_por_id_usuarios_saas',
        'paradas_detectadas', 'usuarios_saas',
        ['exclusion_propuesta_por_id'], ['id'],
    )
    op.create_foreign_key(
        'fk_paradas_detectadas_exclusion_resuelta_por_id_usuarios_saas',
        'paradas_detectadas', 'usuarios_saas',
        ['exclusion_resuelta_por_id'], ['id'],
    )
    # Fase CC: la consulta de aprobación filtra por exclusion_oee =
    # 'propuesta' en cada carga de la cola -- mismo criterio de Fase BW
    # (índices en columnas de filtro caliente, no sólo en FKs/timestamps).
    op.create_index('ix_paradas_detectadas_exclusion_oee', 'paradas_detectadas', ['exclusion_oee'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_paradas_detectadas_exclusion_oee', table_name='paradas_detectadas')
    op.drop_constraint(
        'fk_paradas_detectadas_exclusion_resuelta_por_id_usuarios_saas',
        'paradas_detectadas', type_='foreignkey',
    )
    op.drop_constraint(
        'fk_paradas_detectadas_exclusion_propuesta_por_id_usuarios_saas',
        'paradas_detectadas', type_='foreignkey',
    )
    op.drop_column('paradas_detectadas', 'exclusion_resolucion_nota')
    op.drop_column('paradas_detectadas', 'exclusion_resuelta_at')
    op.drop_column('paradas_detectadas', 'exclusion_resuelta_por_id')
    op.drop_column('paradas_detectadas', 'exclusion_propuesta_at')
    op.drop_column('paradas_detectadas', 'exclusion_propuesta_por_id')
    op.drop_column('paradas_detectadas', 'exclusion_motivo')
    op.drop_column('paradas_detectadas', 'exclusion_oee')

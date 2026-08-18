"""fase_du_soft_delete_paradas_planificadas

Auditoría de backend P0-05 (revisado): eliminar_parada_planificada
(operacion.py) hacía hard-delete real de la fila -- acotado a paradas
origen=PLANIFICADA que todavía no empezaron, nunca a downtime real, pero
igual contradice "no eliminar ni ocultar" (mismo criterio que
recomputo.py, que nunca borra ParadaDetectada). Pasa a soft-delete:
estado=ANULADA + motivo/quién/cuándo.

`estadoparada` es un ENUM nativo de Postgres (038c1f341d45); SQLAlchemy
persiste el NOMBRE del miembro (mayúsculas: PENDIENTE, CLASIFICADA), no
el `.value` -- por eso acá se agrega 'ANULADA', no 'anulada'. Mismo
criterio que f3b8a2d914c7 (rol_encargado): ADD VALUE corre dentro de la
transacción de la migración sin problema porque nunca se usa el valor
nuevo en la misma transacción.

Revision ID: 6d2f69366db6
Revises: b3e6a1c8d247
Create Date: 2026-08-17 21:55:27.511860

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '6d2f69366db6'
down_revision: Union[str, None] = 'b3e6a1c8d247'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE estadoparada ADD VALUE IF NOT EXISTS 'ANULADA'")

    op.add_column('paradas_detectadas', sa.Column('motivo_anulacion', sa.String(), nullable=True))
    op.add_column('paradas_detectadas', sa.Column('anulada_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('paradas_detectadas', sa.Column('anulada_at', sa.DateTime(), nullable=True))
    op.create_foreign_key(
        'fk_paradas_detectadas_anulada_por_id', 'paradas_detectadas',
        'usuarios_saas', ['anulada_por_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_paradas_detectadas_anulada_por_id', 'paradas_detectadas', type_='foreignkey')
    op.drop_column('paradas_detectadas', 'anulada_at')
    op.drop_column('paradas_detectadas', 'anulada_por_id')
    op.drop_column('paradas_detectadas', 'motivo_anulacion')
    # ANULADA en el ENUM queda -- Postgres no tiene DROP VALUE (mismo
    # criterio que f3b8a2d914c7).

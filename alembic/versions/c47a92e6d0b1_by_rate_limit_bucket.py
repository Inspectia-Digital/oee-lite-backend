"""by_rate_limit_bucket

Fase BY (auditoría de robustez, batch 3): tabla de contadores para el
rate limiting basado en Postgres (ver app/core/rate_limit.py -- el
porqué de no usar un limiter in-memory está documentado ahí, en
resumen: Cloud Run corre hasta 5 instancias con min-instances=0, un
contador en memoria de proceso no protegería nada de forma consistente).

`clave` es de propósito general (ej. "m2m_auth:1.2.3.4",
"login_operario:<tenant>:<estacion>", "api_key_create:<usuario>") --
un solo bucket sirve para cualquier endpoint que necesite un límite,
sin tabla nueva por caso de uso.

Revision ID: c47a92e6d0b1
Revises: b2c8f4a17e93
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c47a92e6d0b1'
down_revision: Union[str, None] = 'b2c8f4a17e93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rate_limit_bucket',
        sa.Column('clave', sa.String(), nullable=False),
        sa.Column('ventana_inicio', sa.DateTime(), nullable=False),
        sa.Column('intentos', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('clave'),
    )


def downgrade() -> None:
    op.drop_table('rate_limit_bucket')

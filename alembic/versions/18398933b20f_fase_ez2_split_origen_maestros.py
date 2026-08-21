"""fase_ez2_split_origen_maestros

Fase EZ.2 (pedido del usuario, auditoría post go-live Green Mills):
Tenant.origen_maestros gobernaba SKUs, Planes y Órdenes juntos con un
solo toggle tenant-wide (Fase AF/BC) -- un tenant no podía cargar SKUs a
mano y recibir Planes/Órdenes del ERP (o viceversa). Se divide en dos
campos independientes: `origen_maestros` sigue gobernando SKUs;
`origen_maestros_planes` (nuevo) gobierna Planes y Órdenes.

Backfill: se copia el valor actual de `origen_maestros` a
`origen_maestros_planes` para cada tenant -- así ningún tenant que hoy
está en modo "ERP" pierde el bloqueo de altas manuales de Planes/Órdenes
de un día para el otro; a partir de acá, Gerencia puede desacoplarlos
desde el panel si lo necesita.

Revision ID: 18398933b20f
Revises: d3f7a9c1e4b6
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '18398933b20f'
down_revision: Union[str, None] = 'd3f7a9c1e4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants_saas', sa.Column(
        'origen_maestros_planes', sqlmodel.sql.sqltypes.AutoString(),
        nullable=False, server_default='MANUAL',
    ))
    # Backfill: mismo valor que origen_maestros tenía al momento de la
    # migración, no un default ciego a "MANUAL" -- ver docstring arriba.
    op.execute("UPDATE tenants_saas SET origen_maestros_planes = origen_maestros")


def downgrade() -> None:
    op.drop_column('tenants_saas', 'origen_maestros_planes')

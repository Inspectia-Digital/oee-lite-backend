"""plan_nombre

Unificación UX Planes/Órdenes/SKUs (pedido Green Mills, revisión de la
carga de datos): PlanProduccion gana `nombre` (nullable) -- antes un plan
sólo se identificaba por (línea, fecha_inicio) y la única UI (Supervisor,
"Plan del día") asumía que había a lo sumo uno abierto por línea/día. Ahora
puede haber varios el mismo día y hace falta poder nombrarlos/distinguirlos.
Sin backfill: los planes ya creados no tienen un nombre razonable que
inventarles -- la API exige nombre no vacío desde acá en adelante para
altas nuevas (ver PlanProduccionCreate en configuracion.py), los planes
viejos se muestran con un fallback en el front ("Línea — fecha").

Revision ID: a1c4f6e0b912
Revises: 389e8e2190e8
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4f6e0b912'
down_revision: Union[str, None] = '389e8e2190e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('planes_produccion', sa.Column('nombre', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('planes_produccion', 'nombre')

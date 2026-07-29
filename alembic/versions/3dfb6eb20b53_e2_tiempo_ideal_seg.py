"""e2_tiempo_ideal_seg

Fase E2 del hardening production-grade. Agrega tiempo_ideal_seg a
lite_eventos_produccion: snapshot inmutable del tiempo ideal POR UNIDAD
(umbral del SKU activo si había uno, si no el de la estación) en el
momento del escaneo. Necesario para que Rendimiento respete "el umbral
SKU prevalece" incluso para eventos históricos, ya que
estacion.sku_activo_fk es mutable y no se puede reconstruir
retroactivamente qué SKU corría en un evento pasado.

Igual que en migraciones anteriores, el autogenerate de Alembic trajo
varios falsos positivos (nullable=True en columnas enum, "removed
index" sobre los índices parciales existentes) que no reflejan un
cambio real de modelo. Se descartaron a mano.

Revision ID: 3dfb6eb20b53
Revises: c941d511ae50
Create Date: 2026-07-29 15:00:00.000000

"""
import sqlalchemy as sa
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3dfb6eb20b53'
down_revision: Union[str, None] = 'c941d511ae50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'lite_eventos_produccion',
        sa.Column('tiempo_ideal_seg', sa.Float(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('lite_eventos_produccion', 'tiempo_ideal_seg')

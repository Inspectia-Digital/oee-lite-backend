"""n_parada_origen_y_tenant_oee_objetivo

Fase N del hardening (auditoría de producción del front, ítems 8/9/10):

- paradas_detectadas.origen: distingue paradas detectadas automáticamente
  por gap de scans (AUTOMATICA) de las cargadas de antemano por un
  supervisor (PLANIFICADA, vía /paradas/planificadas). Antes ambas
  terminaban con estado=CLASIFICADA sin forma de diferenciarlas -- necesario
  para separar "historial de clasificadas" de "programadas" en el front.
- tenants_saas.oee_objetivo_pct: objetivo de OEE configurable por tenant
  (antes hardcodeado en el front: 75 en la tendencia, 85 en el Command
  Center).

Mismos falsos positivos de autogenerate de siempre (índices parciales y
nullable de columnas enum) -- descartados a mano, sólo quedan los dos
add_column reales.

Revision ID: 359e751013b1
Revises: 7af24f2546a7
Create Date: 2026-08-05 16:14:39.173022

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '359e751013b1'
down_revision: Union[str, None] = '7af24f2546a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'paradas_detectadas',
        sa.Column('origen', sa.String(), nullable=False, server_default='AUTOMATICA'),
    )
    op.add_column(
        'tenants_saas',
        sa.Column('oee_objetivo_pct', sa.Float(), nullable=False, server_default='85.0'),
    )


def downgrade() -> None:
    op.drop_column('tenants_saas', 'oee_objetivo_pct')
    op.drop_column('paradas_detectadas', 'origen')

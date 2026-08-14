"""bw_indices_performance_paradas_eventos

Fase BW (auditoría de robustez, batch 3): `paradas_detectadas` no tenía
ningún índice propio más allá del heredado de `tenant_id` (TenantBase) --
`listar_historial_paradas` (operacion.py) filtra por tenant/estado/origen
y siempre ordena por `inicio DESC`, sin ningún índice que lo soporte.
`lite_eventos_produccion` sí tenía índices individuales en varias columnas
pero ninguno compuesto, pese a que casi todas las queries de analytics.py
filtran por la combinación (tenant_id, timestamp) o (id_estacion,
timestamp) en simultáneo.

Índices nuevos:
- paradas_detectadas(tenant_id, inicio): matchea el filtro+ORDER BY del
  historial completo.
- paradas_detectadas(estacion_fk): usado en joins/filtros de paradas por
  estación (paradas-pendientes, clasificar).
- paradas_detectadas(estado): PENDIENTE es el filtro más frecuente (cola
  de paradas pendientes).
- lite_eventos_produccion(tenant_id, timestamp): reporte-produccion,
  oee-tendencia y el resto de analytics.py filtran por esta combinación.
- lite_eventos_produccion(id_estacion, timestamp): consultas por
  estación+rango (scans.py, resolución de eventos recientes).

Revision ID: b2c8f4a17e93
Revises: e1b7c4a9f2d6
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b2c8f4a17e93'
down_revision: Union[str, None] = 'e1b7c4a9f2d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_paradas_detectadas_tenant_inicio', 'paradas_detectadas',
        ['tenant_id', 'inicio'],
    )
    op.create_index('ix_paradas_detectadas_estacion_fk', 'paradas_detectadas', ['estacion_fk'])
    op.create_index('ix_paradas_detectadas_estado', 'paradas_detectadas', ['estado'])
    op.create_index(
        'ix_lite_eventos_produccion_tenant_timestamp', 'lite_eventos_produccion',
        ['tenant_id', 'timestamp'],
    )
    op.create_index(
        'ix_lite_eventos_produccion_estacion_timestamp', 'lite_eventos_produccion',
        ['id_estacion', 'timestamp'],
    )


def downgrade() -> None:
    op.drop_index('ix_lite_eventos_produccion_estacion_timestamp', table_name='lite_eventos_produccion')
    op.drop_index('ix_lite_eventos_produccion_tenant_timestamp', table_name='lite_eventos_produccion')
    op.drop_index('ix_paradas_detectadas_estado', table_name='paradas_detectadas')
    op.drop_index('ix_paradas_detectadas_estacion_fk', table_name='paradas_detectadas')
    op.drop_index('ix_paradas_detectadas_tenant_inicio', table_name='paradas_detectadas')

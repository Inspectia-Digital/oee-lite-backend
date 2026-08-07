"""q_dias_semana_umbrales_linea_asignacion_recurrente

Fase Q (feedback de producto sobre la app en uso por el equipo):

- dim_turnos.dias_semana: CSV de días ISO (1=lunes..7=domingo, mismo
  esquema que datetime.isoweekday()) en que aplica el turno. Antes no
  existía -- un turno aplicaba todos los días sin excepción, lo cual no
  sirve para empresas Lu-Vi, Lu-Sa, Lu-Do, etc. Default
  '1,2,3,4,5,6,7' preserva el comportamiento anterior de los turnos
  existentes sin backfill adicional.

- dim_lineas.umbral_optimo/lento/alerta: default heredable por línea
  para las estaciones que no configuren el suyo propio (NULL = sin
  default de línea). dim_estaciones.umbral_* pasa de NOT NULL con
  default 240/280/300 a NULLABLE (NULL = hereda de la línea, y si la
  línea tampoco lo tiene, el sistema cae al default histórico 240/280/
  300 en código -- ver scans.py). No reemplaza
  tenants_saas.tolerancia_lento_pct/alerta_pct: ese es un concepto
  distinto (% de tolerancia sobre el ciclo ideal del SKU activo,
  Fase P), no tocado acá.

- asignaciones_supervisor: reemplaza el modelo "un supervisor por
  línea+turno+día EXACTO" (una fila por fecha) por una REGLA
  recurrente (dias_semana + vigencia_desde/hasta, sin fecha exacta).
  Backfill: cada fila existente se convierte en una regla de un solo
  día (dias_semana = día ISO de esa fecha, vigencia_desde =
  vigencia_hasta = esa fecha) antes de borrar la columna `fecha`.

Revision ID: f67b61f36bcf
Revises: 359e751013b1
Create Date: 2026-08-07 16:09:05.513973

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f67b61f36bcf'
down_revision: Union[str, None] = '359e751013b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Turnos: días de semana ---
    op.add_column(
        'dim_turnos',
        sa.Column('dias_semana', sa.String(), nullable=False, server_default='1,2,3,4,5,6,7'),
    )
    # El server_default sólo hacía falta para poblar las filas existentes
    # al agregar una columna NOT NULL -- se saca después: el modelo ya
    # manda el valor explícito en cada INSERT nuevo, no hace falta que la
    # base también lo infiera.
    op.alter_column('dim_turnos', 'dias_semana', server_default=None)

    # --- Líneas: umbrales default heredables ---
    op.add_column('dim_lineas', sa.Column('umbral_optimo', sa.Integer(), nullable=True))
    op.add_column('dim_lineas', sa.Column('umbral_lento', sa.Integer(), nullable=True))
    op.add_column('dim_lineas', sa.Column('umbral_alerta', sa.Integer(), nullable=True))

    # --- Estaciones: umbrales pasan a nullable (NULL = hereda de línea) ---
    op.alter_column('dim_estaciones', 'umbral_optimo', existing_type=sa.Integer(), nullable=True)
    op.alter_column('dim_estaciones', 'umbral_lento', existing_type=sa.Integer(), nullable=True)
    op.alter_column('dim_estaciones', 'umbral_alerta', existing_type=sa.Integer(), nullable=True)

    # --- Asignación de supervisores: de "un día exacto" a regla recurrente ---
    op.add_column('asignaciones_supervisor', sa.Column('dias_semana', sa.String(), nullable=True))
    op.add_column('asignaciones_supervisor', sa.Column('vigencia_desde', sa.Date(), nullable=True))
    op.add_column('asignaciones_supervisor', sa.Column('vigencia_hasta', sa.Date(), nullable=True))

    # EXTRACT(ISODOW FROM fecha) de Postgres devuelve 1=lunes..7=domingo,
    # exactamente el mismo esquema que datetime.isoweekday() de Python --
    # no hace falta traducir nada entre los dos lados.
    op.execute("""
        UPDATE asignaciones_supervisor
        SET dias_semana = EXTRACT(ISODOW FROM fecha)::text,
            vigencia_desde = fecha,
            vigencia_hasta = fecha
        WHERE fecha IS NOT NULL
    """)

    op.alter_column('asignaciones_supervisor', 'dias_semana', existing_type=sa.String(), nullable=False)
    op.alter_column('asignaciones_supervisor', 'vigencia_desde', existing_type=sa.Date(), nullable=False)
    op.drop_column('asignaciones_supervisor', 'fecha')


def downgrade() -> None:
    op.add_column('asignaciones_supervisor', sa.Column('fecha', sa.Date(), nullable=True))
    op.execute("UPDATE asignaciones_supervisor SET fecha = vigencia_desde WHERE fecha IS NULL")
    op.alter_column('asignaciones_supervisor', 'fecha', existing_type=sa.Date(), nullable=False)
    op.drop_column('asignaciones_supervisor', 'vigencia_hasta')
    op.drop_column('asignaciones_supervisor', 'vigencia_desde')
    op.drop_column('asignaciones_supervisor', 'dias_semana')

    # Backfill de NULLs antes de reimponer NOT NULL: cualquier estación
    # creada después de esta migración pudo haber quedado con umbral_*
    # en NULL (heredando de la línea) -- el downgrade tiene que llenarlos
    # con el default histórico o el ALTER COLUMN ... NOT NULL falla.
    op.execute("UPDATE dim_estaciones SET umbral_optimo = 240 WHERE umbral_optimo IS NULL")
    op.execute("UPDATE dim_estaciones SET umbral_lento = 280 WHERE umbral_lento IS NULL")
    op.execute("UPDATE dim_estaciones SET umbral_alerta = 300 WHERE umbral_alerta IS NULL")
    op.alter_column('dim_estaciones', 'umbral_optimo', existing_type=sa.Integer(), nullable=False, server_default='240')
    op.alter_column('dim_estaciones', 'umbral_lento', existing_type=sa.Integer(), nullable=False, server_default='280')
    op.alter_column('dim_estaciones', 'umbral_alerta', existing_type=sa.Integer(), nullable=False, server_default='300')

    op.drop_column('dim_lineas', 'umbral_alerta')
    op.drop_column('dim_lineas', 'umbral_lento')
    op.drop_column('dim_lineas', 'umbral_optimo')

    op.drop_column('dim_turnos', 'dias_semana')

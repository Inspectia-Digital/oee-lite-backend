"""ac_rediseno_umbrales_perfil_tiempos

Fase AC (pedido explícito de Green Mills, "hay que simplificar la lógica,
hacerla robusta, clara y con una UX comprensible para la configuración"):
reemplaza el modelo de Fases Q/R (umbral_optimo/lento/alerta en segundos +
tolerancia_lento/alerta_pct en %, repartidos en 4 niveles -- Estación,
Línea, Tenant, más el SKU) por UN solo concepto en TODA la cascada: un
"perfil de tiempos" son siempre 3 números en segundos (ideal, lento,
alerta), nunca porcentaje. Cascada SKU×Estación -> SKU -> Línea (ver
app/core/clasificacion.py). Estación y Empresa/Tenant dejan de ser
niveles de la cascada.

- tenants_saas: DROP tolerancia_lento_pct/alerta_pct (Empresa ya no es
  un nivel -- no aportaba nada real, un único % para todo el tenant sin
  relación con el ritmo de cada línea).

- dim_lineas: DROP los 5 campos viejos (umbral_optimo/lento/alerta,
  tolerancia_lento/alerta_pct). ADD tiempo_ideal_seg/tiempo_lento_seg/
  tiempo_alerta_seg, NOT NULL con default 240/280/300 (mismos valores
  que antes eran la constante hardcodeada de sistema en clasificacion.py
  -- ahora son datos editables por línea desde el alta, nunca más un
  número fijo en el código). Línea pasa a ser el ÚNICO piso de la
  cascada.

- dim_estaciones: DROP los mismos 5 campos, SIN reemplazo -- Estación
  deja de tener un perfil de tiempos propio independiente de un SKU.

- maestro_skus: RENAME tiempo_ciclo_teorico -> tiempo_ideal_seg (mismo
  dato, nombre consistente con Linea/SkuTiempoEstacion). ADD
  tiempo_lento_seg/tiempo_alerta_seg NULLABLE, sin backfill -- todo SKU
  existente arranca con un perfil incompleto (cae al piso de Línea)
  hasta que alguien lo complete a propósito. No hay forma sensata de
  derivar estos dos campos nuevos de ningún dato viejo (el modelo
  anterior no tenía lento/alerta por SKU, sólo el % de tolerancia del
  tenant) -- confirmado con el usuario: "empecemos todo de cero, limpio
  de entrada".

- sku_tiempo_estacion: mismo criterio "de cero" pero más fuerte -- esta
  tabla es sólo overrides puntuales (no un catálogo que haya que
  preservar como maestro_skus), y el nuevo perfil exige 3 campos NOT
  NULL siempre completos (antes era 1 solo campo). No hay backfill
  razonable de un override de 1 campo a uno de 3 -- se BORRAN las filas
  existentes (DELETE, explícitamente autorizado por el usuario) antes
  de renombrar/agregar columnas, así el ALTER a NOT NULL no necesita
  inventar valores. Quien tuviera un override cargado lo recarga con el
  perfil completo nuevo (eran pocos: overrides puntuales, no el catálogo).

Revision ID: 72e94de52591
Revises: 58d82d942308
Create Date: 2026-08-12 15:37:43.092915

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '72e94de52591'
down_revision: Union[str, None] = '58d82d942308'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Tenant: Empresa deja de ser un nivel de la cascada ---
    op.drop_column('tenants_saas', 'tolerancia_alerta_pct')
    op.drop_column('tenants_saas', 'tolerancia_lento_pct')

    # --- Línea: pasa a ser el ÚNICO piso, perfil de 3 tiempos siempre completo ---
    op.add_column('dim_lineas', sa.Column('tiempo_ideal_seg', sa.Float(), nullable=False, server_default='240.0'))
    op.add_column('dim_lineas', sa.Column('tiempo_lento_seg', sa.Float(), nullable=False, server_default='280.0'))
    op.add_column('dim_lineas', sa.Column('tiempo_alerta_seg', sa.Float(), nullable=False, server_default='300.0'))
    # El server_default sólo hacía falta para poblar las líneas existentes
    # -- se saca después, mismo criterio que el resto de las migraciones
    # de este repo (el modelo ya manda el valor explícito en cada INSERT).
    op.alter_column('dim_lineas', 'tiempo_ideal_seg', server_default=None)
    op.alter_column('dim_lineas', 'tiempo_lento_seg', server_default=None)
    op.alter_column('dim_lineas', 'tiempo_alerta_seg', server_default=None)

    op.drop_column('dim_lineas', 'tolerancia_alerta_pct')
    op.drop_column('dim_lineas', 'tolerancia_lento_pct')
    op.drop_column('dim_lineas', 'umbral_alerta')
    op.drop_column('dim_lineas', 'umbral_lento')
    op.drop_column('dim_lineas', 'umbral_optimo')

    # --- Estación: deja de tener perfil de tiempos propio ---
    op.drop_column('dim_estaciones', 'tolerancia_alerta_pct')
    op.drop_column('dim_estaciones', 'tolerancia_lento_pct')
    op.drop_column('dim_estaciones', 'umbral_alerta')
    op.drop_column('dim_estaciones', 'umbral_lento')
    op.drop_column('dim_estaciones', 'umbral_optimo')

    # --- MaestroSKU: perfil completo (rename + 2 campos nuevos, sin backfill) ---
    op.alter_column('maestro_skus', 'tiempo_ciclo_teorico', new_column_name='tiempo_ideal_seg')
    op.add_column('maestro_skus', sa.Column('tiempo_lento_seg', sa.Float(), nullable=True))
    op.add_column('maestro_skus', sa.Column('tiempo_alerta_seg', sa.Float(), nullable=True))

    # --- SkuTiempoEstacion: "de cero" -- se borran los overrides viejos ---
    # (1 solo campo, tolerancia % implícita del tenant) porque no hay
    # backfill razonable hacia el nuevo perfil de 3 campos NOT NULL.
    op.execute("DELETE FROM sku_tiempo_estacion")
    op.alter_column('sku_tiempo_estacion', 'tiempo_ciclo_teorico', new_column_name='tiempo_ideal_seg')
    op.add_column('sku_tiempo_estacion', sa.Column('tiempo_lento_seg', sa.Float(), nullable=False))
    op.add_column('sku_tiempo_estacion', sa.Column('tiempo_alerta_seg', sa.Float(), nullable=False))


def downgrade() -> None:
    op.drop_column('sku_tiempo_estacion', 'tiempo_alerta_seg')
    op.drop_column('sku_tiempo_estacion', 'tiempo_lento_seg')
    op.alter_column('sku_tiempo_estacion', 'tiempo_ideal_seg', new_column_name='tiempo_ciclo_teorico')
    # Los overrides borrados en upgrade() NO se recuperan -- downgrade
    # vuelve a la FORMA de la tabla de antes de Fase AC, no a sus datos
    # (imposible: el DELETE de upgrade() es irreversible por diseño,
    # igual que cualquier otro downgrade de una migración destructiva).

    op.drop_column('maestro_skus', 'tiempo_alerta_seg')
    op.drop_column('maestro_skus', 'tiempo_lento_seg')
    op.alter_column('maestro_skus', 'tiempo_ideal_seg', new_column_name='tiempo_ciclo_teorico')

    # --- Estación: restaura columnas en el estado post-Fase Q/R (nullable) ---
    op.add_column('dim_estaciones', sa.Column('umbral_optimo', sa.Integer(), nullable=True))
    op.add_column('dim_estaciones', sa.Column('umbral_lento', sa.Integer(), nullable=True))
    op.add_column('dim_estaciones', sa.Column('umbral_alerta', sa.Integer(), nullable=True))
    op.add_column('dim_estaciones', sa.Column('tolerancia_lento_pct', sa.Float(), nullable=True))
    op.add_column('dim_estaciones', sa.Column('tolerancia_alerta_pct', sa.Float(), nullable=True))

    # --- Línea: restaura columnas viejas, DROP el perfil nuevo ---
    op.add_column('dim_lineas', sa.Column('umbral_optimo', sa.Integer(), nullable=True))
    op.add_column('dim_lineas', sa.Column('umbral_lento', sa.Integer(), nullable=True))
    op.add_column('dim_lineas', sa.Column('umbral_alerta', sa.Integer(), nullable=True))
    op.add_column('dim_lineas', sa.Column('tolerancia_lento_pct', sa.Float(), nullable=True))
    op.add_column('dim_lineas', sa.Column('tolerancia_alerta_pct', sa.Float(), nullable=True))

    op.drop_column('dim_lineas', 'tiempo_alerta_seg')
    op.drop_column('dim_lineas', 'tiempo_lento_seg')
    op.drop_column('dim_lineas', 'tiempo_ideal_seg')

    # --- Tenant: restaura tolerancia_*_pct ---
    # No se puede reconstruir el valor real que tenía cada tenant antes
    # del DROP (ej. green_mills tenía 1.20/1.30, distinto del default) --
    # mismo criterio ya aceptado en este repo para downgrades de columnas
    # dropeadas (ver f67b61f36bcf). Se restaura con el default original
    # de Tenant (1.15/1.25); si hace falta el valor real de un tenant
    # puntual, se re-carga a mano después del downgrade.
    op.add_column('tenants_saas', sa.Column('tolerancia_lento_pct', sa.Float(), nullable=False, server_default='1.15'))
    op.add_column('tenants_saas', sa.Column('tolerancia_alerta_pct', sa.Float(), nullable=False, server_default='1.25'))
    op.alter_column('tenants_saas', 'tolerancia_lento_pct', server_default=None)
    op.alter_column('tenants_saas', 'tolerancia_alerta_pct', server_default=None)

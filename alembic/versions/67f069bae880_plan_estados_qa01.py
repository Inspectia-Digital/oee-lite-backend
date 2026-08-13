"""plan_estados_qa01

QA-01 (auditoría QA post-Fase AG): el modelo anterior de PlanProduccion
(ABIERTO/CERRADO) no tenía invariante de unicidad -- podían coexistir
varios planes ABIERTO en la misma línea, y resolver_orden_activa
(clasificacion.py) tomaba cualquiera con `.first()` (orden no
determinístico de Postgres), contaminando qué orden/SKU/perfil de
tiempos se le atribuía a cada scan de esa línea. Decisión del usuario:
modelo de 5 estados (BORRADOR/PROGRAMADO/EN_PROGRESO/CERRADO/CANCELADO),
con EN_PROGRESO como único estado operativo, garantizado único por
(tenant_id, linea_id) también a nivel de base de datos -- no sólo por
el código de la app.

`estado` es un VARCHAR simple (sin ENUM nativo de Postgres, ver
58d82d942308_aa_plan_produccion -- a diferencia de RolUsuario, acá NO
hace falta ALTER TYPE), así que no hay gotcha de enum nativo. Lo que sí
hace falta es una transformación de datos ANTES de crear el índice
único parcial:

1. De-duplicar: si alguna (tenant_id, linea_id) tiene HOY más de un
   plan 'abierto' activo (el bug mismo que se está corrigiendo), se
   queda con el de fecha_inicio más reciente como candidato a
   'en_progreso' -- el resto baja a 'programado' (no se cancelan ni se
   tocan sus órdenes, sólo dejan de competir por la línea; un humano
   puede reactivar el que corresponda después vía POST .../activar).
   Empate de fecha_inicio se rompe por id descendente (determinístico,
   sin significado de negocio particular).
2. Renombrar el resto de 'abierto' -> 'en_progreso' (mismo significado
   que tenía antes bajo el nombre viejo).
3. Recién ahí crear el índice único parcial -- si el paso 1 no
   de-duplicara primero, este CREATE UNIQUE INDEX fallaría directo
   contra cualquier tenant que ya tenga el bug en producción.

Revision ID: 67f069bae880
Revises: f3b8a2d914c7
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '67f069bae880'
down_revision: Union[str, None] = 'f3b8a2d914c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. De-dup defensivo (ver docstring) -- no-op si ya no hay ninguna
    # línea con más de un plan 'abierto' activo.
    op.execute("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY tenant_id, linea_id
                       ORDER BY fecha_inicio DESC, id DESC
                   ) AS rn
            FROM planes_produccion
            WHERE estado = 'abierto' AND activo = true
        )
        UPDATE planes_produccion p
        SET estado = 'programado'
        FROM ranked r
        WHERE p.id = r.id AND r.rn > 1
    """)

    # 2. El resto de 'abierto' (el ganador de cada línea, o los que ya
    # eran únicos) pasan a 'en_progreso'.
    op.execute("UPDATE planes_produccion SET estado = 'en_progreso' WHERE estado = 'abierto'")

    # 3. Constraint real: nunca más de un plan en_progreso+activo por
    # línea, garantizado por Postgres.
    op.execute("""
        CREATE UNIQUE INDEX ix_plan_unico_en_progreso_por_linea
        ON planes_produccion (tenant_id, linea_id)
        WHERE estado = 'en_progreso' AND activo = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_plan_unico_en_progreso_por_linea")
    # La distinción programado/borrador vs en_progreso se pierde acá --
    # aceptable para un downgrade de emergencia, vuelve exactamente al
    # modelo viejo (2 estados, sin invariante de unicidad).
    op.execute("UPDATE planes_produccion SET estado = 'abierto' WHERE estado IN ('en_progreso', 'programado', 'borrador')")
    op.execute("UPDATE planes_produccion SET estado = 'cerrado' WHERE estado = 'cancelado'")

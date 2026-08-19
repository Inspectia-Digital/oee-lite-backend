"""seed_catalogo_modulos_disponibles

Fase EK.1 (plan "Unificación mínima de módulos"). Arregla la causa raíz
de un bug real reportado por el usuario: el switch de asignar módulos en
`TenantEditDialog` (frontend) se revertía solo -- `PATCH
/accesos/superadmin/tenants/{id}/modulos` (admin.py) valida cada código
contra `modulos_disponibles`, y esa tabla queda VACÍA después de
`7551b883d817_fase_eb_billing_catalogo` (sólo crea la tabla). El único
seed real de datos vivía en `seed.py:156-171`, documentado ahí mismo como
paso OPCIONAL de desarrollo -- nunca se corrió en staging/producción, así
que en cualquier ambiente real cualquier intento de AGREGAR un módulo
devolvía 422 "Módulos inválidos: [...]. Válidos: []" (confirmado, ver
`tests/test_modulos_contratados.py:1-13`, que ya documentaba este bug).

Se migran los mismos 5 códigos/nombres/descripciones/orden/estado que ya
usa `seed.py` (que a su vez espeja `MODULE_CATALOG` hardcodeado del
frontend, `oee-lite/src/admin/modules.ts:14-20`) -- no se inventa
catálogo nuevo, se lo hace correr siempre (`alembic upgrade head`) en vez
de depender de un script manual que nadie corrió en un ambiente real.
`ON CONFLICT (codigo) DO NOTHING`: si `seed.py` ya sembró esta misma fila
en un ambiente (ej. dev local), la migración no la duplica ni la pisa.

`downgrade()` es un no-op deliberado: para cuando esto corra en cualquier
ambiente real, es probable que ya existan `planes_precio` y/o
`tenant_modulos_asignados` colgando de estas filas (FK); borrarlas
rompería esas referencias. Bajar esta migración sin borrar el catálogo
no reintroduce el bug (la tabla simplemente queda con datos, que es el
estado correcto).

Revision ID: a1c3e8f56b02
Revises: 5fde6f43bdb4
Create Date: 2026-08-18 18:00:00.000000

"""
import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c3e8f56b02'
down_revision: Union[str, None] = '5fde6f43bdb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mismos códigos/nombres/descripción/orden/estado que seed.py:156-162 --
# fuente única, no se inventa nada acá.
MODULOS_SEED = [
    ("tymeo", "TYMEO", "OEE y Producción", 1, "activo"),
    ("oee-hub", "OEE Hub", "Factory OEE Hub", 2, "proximamente"),
    ("vision", "InspectIA Vision", "Visión artificial de calidad", 3, "proximamente"),
    ("logistica", "Logística", "Trazabilidad logística", 4, "proximamente"),
    ("seguridad", "Seguridad", "Seguridad y prevención", 5, "proximamente"),
]


def upgrade() -> None:
    ahora = datetime.utcnow()
    tabla = sa.table(
        "modulos_disponibles",
        sa.column("id", sa.dialects.postgresql.UUID()),
        sa.column("codigo", sa.String()),
        sa.column("nombre", sa.String()),
        sa.column("descripcion", sa.String()),
        sa.column("orden", sa.Integer()),
        sa.column("estado", sa.Enum(name="estadomodulodisponible")),
        sa.column("creado_at", sa.DateTime()),
        sa.column("actualizado_at", sa.DateTime()),
    )
    insert_stmt = sa.dialects.postgresql.insert(tabla).values([
        {
            "id": uuid.uuid4(),
            "codigo": codigo,
            "nombre": nombre,
            "descripcion": descripcion,
            "orden": orden,
            "estado": estado,
            "creado_at": ahora,
            "actualizado_at": ahora,
        }
        for codigo, nombre, descripcion, orden, estado in MODULOS_SEED
    ]).on_conflict_do_nothing(index_elements=["codigo"])
    op.execute(insert_stmt)


def downgrade() -> None:
    # No-op deliberado -- ver docstring del módulo.
    pass

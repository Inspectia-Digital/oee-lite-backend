"""rol_encargado

Pedido explícito del usuario: rol nuevo "Encargado", intermedio entre
Supervisor y Operario -- el supervisor a veces está con otras tareas y no
puede estar atento a clasificar paradas. Encargado tiene cuenta web pero
acceso angosto: sólo paradas pendientes/clasificar/historial (ver
ROLES_SUPERVISION_COMPLETA en operacion.py, que lo excluye explícitamente
del resto).

`rolusuario` es un ENUM nativo de Postgres (creado en 038c1f341d45); los
valores que persiste SQLAlchemy son el NOMBRE del miembro de RolUsuario
(mayúsculas: SUPERADMIN, GERENCIA, ...), no el `.value` en minúsculas --
por eso acá se agrega 'ENCARGADO', no 'encargado'.

ALTER TYPE ... ADD VALUE no necesita fuera-de-transacción desde Postgres
12 (este proyecto corre 15) siempre que el valor nuevo no se USE en la
misma transacción en que se agrega -- esta migración sólo agrega el
valor, nunca lo referencia, así que corre sin problema dentro de la
transacción que ya envuelve cada migración de Alembic.

downgrade() es un no-op documentado: Postgres no tiene `DROP VALUE` para
enums -- sacar un valor exige recrear el tipo entero (crear uno nuevo sin
el valor, migrar la columna, borrar el viejo, renombrar), lo cual además
rompería si ya existiera algún UsuarioSaaS con rol='ENCARGADO'. No vale
la complejidad para un rol recién agregado sin usuarios reales todavía.

Revision ID: f3b8a2d914c7
Revises: a1c4f6e0b912
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3b8a2d914c7'
down_revision: Union[str, None] = 'a1c4f6e0b912'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE rolusuario ADD VALUE IF NOT EXISTS 'ENCARGADO'")


def downgrade() -> None:
    # No-op deliberado -- ver docstring del módulo.
    pass

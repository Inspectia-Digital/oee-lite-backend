"""refactor_inspectia_os

Revision ID: 9261c6f3fe42
Revises: 038c1f341d45
Create Date: 2026-07-26 21:29:15.835993

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '9261c6f3fe42'
down_revision: Union[str, None] = '038c1f341d45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Crear TODOS los tipos ENUM explícitamente antes de que Alembic los toque
    op.execute("CREATE TYPE modoasignacionoperariosestacion AS ENUM ('heredar', 'manual', 'escaneo')")
    op.execute("CREATE TYPE modoasignacionoperarios AS ENUM ('manual', 'escaneo')")
    op.execute("CREATE TYPE tipotenant AS ENUM ('empresa', 'planta')")

    # 2. Castear los datos existentes
    op.execute("ALTER TABLE dim_estaciones ALTER COLUMN modo_asignacion_operarios TYPE modoasignacionoperariosestacion USING modo_asignacion_operarios::modoasignacionoperariosestacion")
    op.execute("ALTER TABLE dim_lineas ALTER COLUMN modo_asignacion_operarios TYPE modoasignacionoperarios USING modo_asignacion_operarios::modoasignacionoperarios")
    op.execute("ALTER TABLE tenants_saas ALTER COLUMN modo_asignacion_operarios TYPE modoasignacionoperarios USING modo_asignacion_operarios::modoasignacionoperarios")

    # 3. Columnas nuevas (Alembic ya no fallará porque los tipos existen)
    op.add_column('lite_eventos_produccion', sa.Column('unidades_procesadas', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('maestro_skus', sa.Column('linea_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.create_foreign_key(None, 'maestro_skus', 'dim_lineas', ['linea_id'], ['id'])
    
    op.add_column('tenants_saas', sa.Column('tipo', sa.Enum('empresa', 'planta', name='tipotenant'), nullable=False, server_default='empresa'))
    op.add_column('tenants_saas', sa.Column('parent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('tenants_saas', sa.Column('modulos_contratados', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='tymeo'))
    op.add_column('tenants_saas', sa.Column('theme_default', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('tenants_saas', sa.Column('activo', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('tenants_saas', sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')))
    
    op.create_foreign_key(None, 'tenants_saas', 'tenants_saas', ['parent_id'], ['id'])
    op.create_foreign_key(None, 'usuarios_saas', 'tenants_saas', ['tenant_id'], ['id'])


def downgrade() -> None:
    # 1. Revertir FKs y columnas
    op.drop_constraint(None, 'usuarios_saas', type_='foreignkey')
    op.drop_constraint(None, 'tenants_saas', type_='foreignkey')
    
    op.drop_column('tenants_saas', 'created_at')
    op.drop_column('tenants_saas', 'activo')
    op.drop_column('tenants_saas', 'theme_default')
    op.drop_column('tenants_saas', 'modulos_contratados')
    op.drop_column('tenants_saas', 'parent_id')
    op.drop_column('tenants_saas', 'tipo')
    
    op.drop_constraint(None, 'maestro_skus', type_='foreignkey')
    op.drop_column('maestro_skus', 'linea_id')
    op.drop_column('lite_eventos_produccion', 'unidades_procesadas')

    # 2. Revertir ENUM a VARCHAR
    op.execute("ALTER TABLE tenants_saas ALTER COLUMN modo_asignacion_operarios TYPE VARCHAR USING modo_asignacion_operarios::VARCHAR")
    op.execute("ALTER TABLE dim_lineas ALTER COLUMN modo_asignacion_operarios TYPE VARCHAR USING modo_asignacion_operarios::VARCHAR")
    op.execute("ALTER TABLE dim_estaciones ALTER COLUMN modo_asignacion_operarios TYPE VARCHAR USING modo_asignacion_operarios::VARCHAR")

    # 3. Dropear ENUMs
    op.execute("DROP TYPE modoasignacionoperarios")
    op.execute("DROP TYPE modoasignacionoperariosestacion")
    op.execute("DROP TYPE tipotenant")
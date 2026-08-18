"""fase_eb_billing_catalogo

PRD "Billing MVP + Planes Comerciales" v2.0. Catálogo global (no
tenant-scoped) de módulos/planes de precio/métodos de pago -- primer
paso del MVP de billing, reemplaza a MODULE_CATALOG (frontend) y
MODULOS_VALIDOS (admin.py). Tablas siguientes del PRD (planes_comerciales,
tenant_modulos_asignados, facturas, pagos_informados, tenant_suscripcion)
llegan en fases aparte (EC-EE).

Generada con `alembic revision --autogenerate` y recortada a mano: el
diff automático también traía drift preexistente y no relacionado de
todo el esquema (ej. `DROP TABLE rate_limit_bucket`, real y en uso desde
Fase BY -- autogenerate lo marca porque nunca tuvo un modelo SQLModel
propio, no porque haya que borrarlo). Sólo quedan acá las 3 tablas
nuevas de esta fase.

Revision ID: 7551b883d817
Revises: 59a7614d4317
Create Date: 2026-08-18 02:19:54.422168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '7551b883d817'
down_revision: Union[str, None] = '59a7614d4317'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'metodos_pago_configurados',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('codigo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('nombre', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('tipo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('detalle', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('orden', sa.Integer(), nullable=False),
        sa.Column('estado', sa.Enum('activo', 'inactivo', name='estadometodopago'), nullable=True),
        sa.Column('creado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
        sa.Column('creado_at', sa.DateTime(), nullable=False),
        sa.Column('actualizado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
        sa.Column('actualizado_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['actualizado_por_id'], ['usuarios_saas.id']),
        sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios_saas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_metodos_pago_configurados_codigo'), 'metodos_pago_configurados', ['codigo'], unique=True)

    op.create_table(
        'modulos_disponibles',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('codigo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('nombre', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('descripcion', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('orden', sa.Integer(), nullable=False),
        sa.Column('estado', sa.Enum('activo', 'beta', 'proximamente', name='estadomodulodisponible'), nullable=True),
        sa.Column('creado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
        sa.Column('creado_at', sa.DateTime(), nullable=False),
        sa.Column('actualizado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
        sa.Column('actualizado_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['actualizado_por_id'], ['usuarios_saas.id']),
        sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios_saas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_modulos_disponibles_codigo'), 'modulos_disponibles', ['codigo'], unique=True)

    op.create_table(
        'planes_precio',
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('modulo_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('codigo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('nombre', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('descripcion', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('precio', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('orden', sa.Integer(), nullable=False),
        sa.Column('limite_usuarios', sa.Integer(), nullable=True),
        sa.Column('limite_plantas', sa.Integer(), nullable=True),
        sa.Column('limite_lineas', sa.Integer(), nullable=True),
        sa.Column('estado', sa.Enum('activo', 'deprecado', name='estadoplanprecio'), nullable=True),
        sa.Column('creado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
        sa.Column('creado_at', sa.DateTime(), nullable=False),
        sa.Column('actualizado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
        sa.Column('actualizado_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['actualizado_por_id'], ['usuarios_saas.id']),
        sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios_saas.id']),
        sa.ForeignKeyConstraint(['modulo_id'], ['modulos_disponibles.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_planes_precio_modulo_codigo', 'planes_precio', ['modulo_id', 'codigo'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_planes_precio_modulo_codigo', table_name='planes_precio')
    op.drop_table('planes_precio')
    op.drop_index(op.f('ix_modulos_disponibles_codigo'), table_name='modulos_disponibles')
    op.drop_table('modulos_disponibles')
    op.drop_index(op.f('ix_metodos_pago_configurados_codigo'), table_name='metodos_pago_configurados')
    op.drop_table('metodos_pago_configurados')
    # Postgres no borra un tipo ENUM solo porque se borró la tabla/columna
    # que lo usaba (a diferencia de un tipo anónimo) -- hay que borrarlo
    # a mano o queda huérfano y una re-ejecución de upgrade() falla con
    # "type already exists" (confirmado corriendo el ciclo down/up antes
    # de este fix).
    sa.Enum(name='estadoplanprecio').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='estadomodulodisponible').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='estadometodopago').drop(op.get_bind(), checkfirst=True)

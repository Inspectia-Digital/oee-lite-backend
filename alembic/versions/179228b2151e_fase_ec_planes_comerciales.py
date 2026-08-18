"""fase_ec_planes_comerciales

Revision ID: 179228b2151e
Revises: 7551b883d817
Create Date: 2026-08-18 02:40:18.476337

Fase EC (PRD "Billing MVP" v2.0): planes_comerciales (descuentos/
bonificación) + las 2 tablas M2M de aplicabilidad + tenant_modulos_asignados
(asignación real de módulo+plan+descuento a un tenant). Igual que la
migración de Fase EB (7551b883d817), --autogenerate arrastró ruido de drift
preexistente no relacionado (índices condicionales, cambios de tipo de
columna en tablas históricas, y de nuevo un intento de
`DROP TABLE rate_limit_bucket`, tabla real en uso de Fase BY) -- recortado a
mano para dejar sólo las 4 tablas nuevas de esta fase.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '179228b2151e'
down_revision: Union[str, None] = '7551b883d817'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('planes_comerciales',
    sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('codigo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('nombre', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('descripcion', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('es_bonificado', sa.Boolean(), nullable=False),
    sa.Column('meses_bonificados', sa.Integer(), nullable=True),
    sa.Column('descuento_porcentaje', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('aplica_a_todos_modulos', sa.Boolean(), nullable=False),
    sa.Column('aplica_a_todos_planes', sa.Boolean(), nullable=False),
    sa.Column('estado', sa.Enum('activo', 'archivado', name='estadoplancomercial'), nullable=True),
    sa.Column('fecha_inicio', sa.Date(), nullable=False),
    sa.Column('fecha_fin', sa.Date(), nullable=True),
    sa.Column('creado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('creado_at', sa.DateTime(), nullable=False),
    sa.Column('actualizado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('actualizado_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['actualizado_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios_saas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_planes_comerciales_codigo'), 'planes_comerciales', ['codigo'], unique=True)

    op.create_table('plan_comercial_modulos',
    sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('plan_comercial_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('modulo_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.ForeignKeyConstraint(['modulo_id'], ['modulos_disponibles.id'], ),
    sa.ForeignKeyConstraint(['plan_comercial_id'], ['planes_comerciales.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_plan_comercial_modulos_unico', 'plan_comercial_modulos', ['plan_comercial_id', 'modulo_id'], unique=True)

    op.create_table('plan_comercial_planes',
    sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('plan_comercial_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('plan_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.ForeignKeyConstraint(['plan_comercial_id'], ['planes_comerciales.id'], ),
    sa.ForeignKeyConstraint(['plan_id'], ['planes_precio.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_plan_comercial_planes_unico', 'plan_comercial_planes', ['plan_comercial_id', 'plan_id'], unique=True)

    op.create_table('tenant_modulos_asignados',
    sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('modulo_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('plan_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('plan_comercial_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('metodo_pago_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('fecha_inicio', sa.Date(), nullable=False),
    sa.Column('fecha_renovacion', sa.Date(), nullable=False),
    sa.Column('precio_base', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('precio_con_descuento', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('estado', sa.Enum('activa', 'suspendida', 'cancelada', name='estadoasignacionmodulo'), nullable=True),
    sa.Column('creado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('creado_at', sa.DateTime(), nullable=False),
    sa.Column('actualizado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('actualizado_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['actualizado_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['metodo_pago_id'], ['metodos_pago_configurados.id'], ),
    sa.ForeignKeyConstraint(['modulo_id'], ['modulos_disponibles.id'], ),
    sa.ForeignKeyConstraint(['plan_comercial_id'], ['planes_comerciales.id'], ),
    sa.ForeignKeyConstraint(['plan_id'], ['planes_precio.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants_saas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tenant_modulos_asignados_tenant_id'), 'tenant_modulos_asignados', ['tenant_id'], unique=False)
    op.create_index('ix_tenant_modulos_asignados_unico', 'tenant_modulos_asignados', ['tenant_id', 'modulo_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_tenant_modulos_asignados_unico', table_name='tenant_modulos_asignados')
    op.drop_index(op.f('ix_tenant_modulos_asignados_tenant_id'), table_name='tenant_modulos_asignados')
    op.drop_table('tenant_modulos_asignados')

    op.drop_index('ix_plan_comercial_planes_unico', table_name='plan_comercial_planes')
    op.drop_table('plan_comercial_planes')

    op.drop_index('ix_plan_comercial_modulos_unico', table_name='plan_comercial_modulos')
    op.drop_table('plan_comercial_modulos')

    op.drop_index(op.f('ix_planes_comerciales_codigo'), table_name='planes_comerciales')
    op.drop_table('planes_comerciales')

    # Postgres no dropea automáticamente los tipos ENUM nombrados al hacer
    # drop_table -- mismo bug ya encontrado y corregido en Fase EB
    # (7551b883d817). Sin esto, un ciclo downgrade→upgrade posterior falla
    # con "type ... already exists".
    sa.Enum(name='estadoasignacionmodulo').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='estadoplancomercial').drop(op.get_bind(), checkfirst=True)

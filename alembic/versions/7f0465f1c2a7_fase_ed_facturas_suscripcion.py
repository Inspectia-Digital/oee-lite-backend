"""fase_ed_facturas_suscripcion

Revision ID: 7f0465f1c2a7
Revises: 179228b2151e
Create Date: 2026-08-18 02:55:27.170145

Fase ED (PRD "Billing MVP" v2.0): facturas (registro interno del monto a
pagar, nunca un documento real) + tenant_suscripcion (resumen cacheado de
deuda). Mismo ruido de drift preexistente no relacionado que las migraciones
de Fase EB/EC (índices condicionales, cambios de tipo de columna en tablas
históricas, y de nuevo un intento de `DROP TABLE rate_limit_bucket`, tabla
real en uso de Fase BY) -- recortado a mano para dejar sólo las 2 tablas
nuevas de esta fase.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '7f0465f1c2a7'
down_revision: Union[str, None] = '179228b2151e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('tenant_suscripcion',
    sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('estado', sa.Enum('activa', 'suspendida', 'cancelada', name='estadosuscripciontenant'), nullable=True),
    sa.Column('deuda_total', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('facturas_vencidas', sa.Integer(), nullable=False),
    sa.Column('estado_cuenta', sa.Enum('al_dia', 'con_deuda', 'vencida', name='estadocuentatenant'), nullable=True),
    sa.Column('actualizado_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants_saas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tenant_suscripcion_tenant_id'), 'tenant_suscripcion', ['tenant_id'], unique=True)

    op.create_table('facturas',
    sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('asignacion_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('numero', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('periodo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('fecha_emision', sa.Date(), nullable=False),
    sa.Column('concepto', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('monto', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('metodo_pago_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
    sa.Column('estado', sa.Enum('pendiente_envio', 'enviada', 'pagada', 'vencida', name='estadofactura'), nullable=True),
    sa.Column('fecha_vencimiento', sa.Date(), nullable=False),
    sa.Column('enviada_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('fecha_envio', sa.DateTime(), nullable=True),
    sa.Column('creado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('creado_at', sa.DateTime(), nullable=False),
    sa.Column('actualizado_por_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
    sa.Column('actualizado_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['actualizado_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['asignacion_id'], ['tenant_modulos_asignados.id'], ),
    sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['enviada_por_id'], ['usuarios_saas.id'], ),
    sa.ForeignKeyConstraint(['metodo_pago_id'], ['metodos_pago_configurados.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants_saas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_facturas_asignacion_periodo_unico', 'facturas', ['asignacion_id', 'periodo'], unique=True)
    op.create_index(op.f('ix_facturas_numero'), 'facturas', ['numero'], unique=True)
    op.create_index(op.f('ix_facturas_periodo'), 'facturas', ['periodo'], unique=False)
    op.create_index(op.f('ix_facturas_tenant_id'), 'facturas', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_facturas_tenant_id'), table_name='facturas')
    op.drop_index(op.f('ix_facturas_periodo'), table_name='facturas')
    op.drop_index(op.f('ix_facturas_numero'), table_name='facturas')
    op.drop_index('ix_facturas_asignacion_periodo_unico', table_name='facturas')
    op.drop_table('facturas')

    op.drop_index(op.f('ix_tenant_suscripcion_tenant_id'), table_name='tenant_suscripcion')
    op.drop_table('tenant_suscripcion')

    # Postgres no dropea automáticamente los tipos ENUM nombrados al hacer
    # drop_table -- mismo bug ya encontrado y corregido en Fase EB/EC.
    sa.Enum(name='estadofactura').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='estadocuentatenant').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='estadosuscripciontenant').drop(op.get_bind(), checkfirst=True)

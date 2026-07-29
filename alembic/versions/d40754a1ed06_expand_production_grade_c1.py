"""expand_production_grade_c1

Fase C1 (expand) del hardening production-grade, ver
HANDOFF_STG_PRODUCTION_GRADE.md. Sólo agrega: nuevas tablas, nuevas
columnas, backfills y saneamiento de huérfanos. No retira nada legacy
(eso es la fase contract, C2).

Revision ID: d40754a1ed06
Revises: 9261c6f3fe42
Create Date: 2026-07-29 08:31:07.482767

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd40754a1ed06'
down_revision: Union[str, None] = '9261c6f3fe42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Namespace fijo y arbitrario para derivar UUIDv5 deterministicos de
# "Planta Default" / "Linea Default" por tenant. No corresponde a ningun
# dominio real; sólo garantiza que el mismo tenant_id siempre produzca
# el mismo UUID.
_NAMESPACE_DEFAULTS = uuid.UUID("6f6e6465-6661-756c-7473-6f6565006c69")


def _uuid5_default_planta(tenant_id: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE_DEFAULTS, f"{tenant_id}:default_planta")


def _uuid5_default_linea(tenant_id: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE_DEFAULTS, f"{tenant_id}:default_linea")


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Tablas nuevas (sin filas existentes; NOT NULL es seguro)
    # ------------------------------------------------------------------
    op.create_table(
        'dim_maquinas',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('codigo_externo', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('nombre', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_dim_maquinas_codigo_externo'), 'dim_maquinas', ['codigo_externo'], unique=False)
    op.create_index(op.f('ix_dim_maquinas_tenant_id'), 'dim_maquinas', ['tenant_id'], unique=False)

    op.create_table(
        'usuario_planta',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('usuario_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('planta_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.ForeignKeyConstraint(['planta_id'], ['plantas.id']),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios_saas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_usuario_planta_tenant_id'), 'usuario_planta', ['tenant_id'], unique=False)

    op.create_table(
        'api_keys_dispositivo',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('key_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('secret_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('estacion_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['estacion_id'], ['dim_estaciones.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_api_keys_dispositivo_key_id'), 'api_keys_dispositivo', ['key_id'], unique=True)
    op.create_index(op.f('ix_api_keys_dispositivo_tenant_id'), 'api_keys_dispositivo', ['tenant_id'], unique=False)

    op.create_table(
        'maquina_estacion',
        sa.Column('tenant_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('maquina_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('estacion_id', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['estacion_id'], ['dim_estaciones.id']),
        sa.ForeignKeyConstraint(['maquina_id'], ['dim_maquinas.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_maquina_estacion_tenant_id'), 'maquina_estacion', ['tenant_id'], unique=False)

    # ------------------------------------------------------------------
    # 2. Columnas nuevas sobre tablas CON filas existentes.
    #    Todas NOT NULL con server_default (igual al default del modelo
    #    Python) para no romper contra datos ya cargados.
    # ------------------------------------------------------------------
    metodo_calidad_enum = postgresql.ENUM('POR_TIEMPO', 'POR_RECHAZO', name='metodocalidadlinea')
    metodo_calidad_enum.create(bind, checkfirst=True)
    op.add_column('dim_lineas', sa.Column(
        'metodo_calidad',
        metodo_calidad_enum,
        nullable=False,
        server_default='POR_RECHAZO',
    ))
    op.add_column('dim_lineas', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('dim_motivos_parada', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('dim_operarios', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('dim_supervisores', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('dim_turnos', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))

    op.add_column('lite_eventos_produccion', sa.Column('maquina_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('lite_eventos_produccion', sa.Column(
        'unidades_rechazadas', sa.Integer(), nullable=False, server_default='0'
    ))
    op.add_column('lite_eventos_produccion', sa.Column('event_id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('lite_eventos_produccion', sa.Column('payload_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    # Backfill histórico obligatorio = True (no hay evidencia de estado histórico real).
    op.add_column('lite_eventos_produccion', sa.Column(
        'incluido_oee', sa.Boolean(), nullable=False, server_default=sa.true()
    ))
    op.create_index(op.f('ix_lite_eventos_produccion_event_id'), 'lite_eventos_produccion', ['event_id'], unique=True)
    op.create_foreign_key(
        'fk_lite_eventos_produccion_maquina_id', 'lite_eventos_produccion', 'dim_maquinas', ['maquina_id'], ['id']
    )
    # Índice obligatorio para Analytics (filtra por tenant + incluido_oee + orden temporal).
    op.create_index(
        'ix_lite_eventos_tenant_incluido_oee_timestamp',
        'lite_eventos_produccion',
        ['tenant_id', 'incluido_oee', 'timestamp'],
    )

    op.add_column('plantas', sa.Column(
        'timezone', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='America/Buenos_Aires'
    ))
    op.add_column('plantas', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))

    estado_tenant_enum = postgresql.ENUM('activo', 'ui_suspendida', 'suspension_total', name='estadotenant')
    estado_tenant_enum.create(bind, checkfirst=True)
    op.add_column('tenants_saas', sa.Column(
        'estado',
        estado_tenant_enum,
        nullable=False,
        server_default='activo',
    ))

    # ------------------------------------------------------------------
    # 3. Identidad interna UUID para MaestroSKU y OrdenProduccion.
    #    codigo_sku/id_orden siguen como PK legacy hasta C2 (contract).
    #    Se agrega 'id' nullable, se backfillea fila por fila y luego se
    #    impone NOT NULL + índice único.
    # ------------------------------------------------------------------
    op.add_column('maestro_skus', sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('maestro_skus', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))

    op.add_column('ordenes_produccion', sa.Column('id', sqlmodel.sql.sqltypes.GUID(), nullable=True))
    op.add_column('ordenes_produccion', sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()))

    skus_sin_id = bind.execute(sa.text("SELECT codigo_sku FROM maestro_skus WHERE id IS NULL")).fetchall()
    for (codigo_sku,) in skus_sin_id:
        bind.execute(
            sa.text("UPDATE maestro_skus SET id = :nuevo_id WHERE codigo_sku = :codigo_sku"),
            {"nuevo_id": str(uuid.uuid4()), "codigo_sku": codigo_sku},
        )
    print(f"[C1] Backfill de id UUID en maestro_skus: {len(skus_sin_id)} filas saneadas.")

    ordenes_sin_id = bind.execute(sa.text("SELECT id_orden FROM ordenes_produccion WHERE id IS NULL")).fetchall()
    for (id_orden,) in ordenes_sin_id:
        bind.execute(
            sa.text("UPDATE ordenes_produccion SET id = :nuevo_id WHERE id_orden = :id_orden"),
            {"nuevo_id": str(uuid.uuid4()), "id_orden": id_orden},
        )
    print(f"[C1] Backfill de id UUID en ordenes_produccion: {len(ordenes_sin_id)} filas saneadas.")

    op.alter_column('maestro_skus', 'id', nullable=False)
    op.create_index(op.f('ix_maestro_skus_id'), 'maestro_skus', ['id'], unique=True)

    op.alter_column('ordenes_produccion', 'id', nullable=False)
    op.create_index(op.f('ix_ordenes_produccion_id'), 'ordenes_produccion', ['id'], unique=True)

    # ------------------------------------------------------------------
    # 4. Saneamiento de huérfanos: Planta Default / Línea Default por
    #    tenant, con UUIDv5 determinístico, antes de imponer NOT NULL.
    # ------------------------------------------------------------------
    tenants = bind.execute(sa.text("SELECT id FROM tenants_saas")).fetchall()

    lineas_huerfanas_total = 0
    estaciones_huerfanas_total = 0

    for (tenant_id,) in tenants:
        lineas_huerfanas = bind.execute(
            sa.text("SELECT id FROM dim_lineas WHERE tenant_id = :tenant_id AND planta_id IS NULL"),
            {"tenant_id": tenant_id},
        ).fetchall()

        if lineas_huerfanas:
            planta_default_id = _uuid5_default_planta(tenant_id)
            existe_planta = bind.execute(
                sa.text("SELECT 1 FROM plantas WHERE id = :id"), {"id": str(planta_default_id)}
            ).fetchone()
            if not existe_planta:
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO plantas (id, tenant_id, nombre, ubicacion, timezone, activo)
                        VALUES (:id, :tenant_id, :nombre, NULL, :timezone, true)
                        """
                    ),
                    {
                        "id": str(planta_default_id),
                        "tenant_id": tenant_id,
                        "nombre": "Planta Default",
                        "timezone": "America/Buenos_Aires",
                    },
                )
            bind.execute(
                sa.text("UPDATE dim_lineas SET planta_id = :planta_id WHERE tenant_id = :tenant_id AND planta_id IS NULL"),
                {"planta_id": str(planta_default_id), "tenant_id": tenant_id},
            )
            lineas_huerfanas_total += len(lineas_huerfanas)

        estaciones_huerfanas = bind.execute(
            sa.text("SELECT id FROM dim_estaciones WHERE tenant_id = :tenant_id AND linea_id IS NULL"),
            {"tenant_id": tenant_id},
        ).fetchall()

        if estaciones_huerfanas:
            linea_default_id = _uuid5_default_linea(tenant_id)
            existe_linea = bind.execute(
                sa.text("SELECT 1 FROM dim_lineas WHERE id = :id"), {"id": str(linea_default_id)}
            ).fetchone()
            if not existe_linea:
                # La línea default también necesita una planta; usamos/creamos la Planta Default del tenant.
                planta_default_id = _uuid5_default_planta(tenant_id)
                existe_planta = bind.execute(
                    sa.text("SELECT 1 FROM plantas WHERE id = :id"), {"id": str(planta_default_id)}
                ).fetchone()
                if not existe_planta:
                    bind.execute(
                        sa.text(
                            """
                            INSERT INTO plantas (id, tenant_id, nombre, ubicacion, timezone, activo)
                            VALUES (:id, :tenant_id, :nombre, NULL, :timezone, true)
                            """
                        ),
                        {
                            "id": str(planta_default_id),
                            "tenant_id": tenant_id,
                            "nombre": "Planta Default",
                            "timezone": "America/Buenos_Aires",
                        },
                    )
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO dim_lineas (
                            id, tenant_id, planta_id, nombre,
                            modo_asignacion_operarios, tipo_produccion, metodo_calidad, activo
                        )
                        VALUES (:id, :tenant_id, :planta_id, :nombre, 'manual', 'DISCRETA', 'POR_RECHAZO', true)
                        """
                    ),
                    {
                        "id": str(linea_default_id),
                        "tenant_id": tenant_id,
                        "planta_id": str(planta_default_id),
                        "nombre": "Línea Default",
                    },
                )
            bind.execute(
                sa.text("UPDATE dim_estaciones SET linea_id = :linea_id WHERE tenant_id = :tenant_id AND linea_id IS NULL"),
                {"linea_id": str(linea_default_id), "tenant_id": tenant_id},
            )
            estaciones_huerfanas_total += len(estaciones_huerfanas)

    print(f"[C1] Líneas huérfanas reasignadas a Planta Default: {lineas_huerfanas_total}.")
    print(f"[C1] Estaciones huérfanas reasignadas a Línea Default: {estaciones_huerfanas_total}.")

    op.alter_column('dim_lineas', 'planta_id', nullable=False)
    op.alter_column('dim_estaciones', 'linea_id', nullable=False)

    # ------------------------------------------------------------------
    # 5. Índices únicos parciales por tenant, sólo sobre filas activas
    #    (permite reutilizar código/legajo cuando el registro anterior
    #    fue dado de baja).
    # ------------------------------------------------------------------
    op.create_index(
        'ux_maestro_skus_codigo_sku_activo',
        'maestro_skus', ['tenant_id', 'codigo_sku'],
        unique=True, postgresql_where=sa.text('activo'),
    )
    op.create_index(
        'ux_ordenes_produccion_id_orden_activo',
        'ordenes_produccion', ['tenant_id', 'id_orden'],
        unique=True, postgresql_where=sa.text('activo'),
    )
    op.create_index(
        'ux_dim_operarios_legajo_activo',
        'dim_operarios', ['tenant_id', 'legajo'],
        unique=True, postgresql_where=sa.text('activo'),
    )
    op.create_index(
        'ux_dim_supervisores_legajo_activo',
        'dim_supervisores', ['tenant_id', 'legajo'],
        unique=True, postgresql_where=sa.text('activo'),
    )
    op.create_index(
        'ux_dim_estaciones_codigo_plc_activo',
        'dim_estaciones', ['tenant_id', 'codigo_plc'],
        unique=True, postgresql_where=sa.text('activa AND codigo_plc IS NOT NULL'),
    )
    op.create_index(
        'ux_dim_maquinas_codigo_externo_activo',
        'dim_maquinas', ['tenant_id', 'codigo_externo'],
        unique=True, postgresql_where=sa.text('activo'),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index('ux_dim_maquinas_codigo_externo_activo', table_name='dim_maquinas')
    op.drop_index('ux_dim_estaciones_codigo_plc_activo', table_name='dim_estaciones')
    op.drop_index('ux_dim_supervisores_legajo_activo', table_name='dim_supervisores')
    op.drop_index('ux_dim_operarios_legajo_activo', table_name='dim_operarios')
    op.drop_index('ux_ordenes_produccion_id_orden_activo', table_name='ordenes_produccion')
    op.drop_index('ux_maestro_skus_codigo_sku_activo', table_name='maestro_skus')

    op.alter_column('dim_estaciones', 'linea_id', nullable=True)
    op.alter_column('dim_lineas', 'planta_id', nullable=True)

    op.drop_index(op.f('ix_ordenes_produccion_id'), table_name='ordenes_produccion')
    op.drop_column('ordenes_produccion', 'activo')
    op.drop_column('ordenes_produccion', 'id')
    op.drop_index(op.f('ix_maestro_skus_id'), table_name='maestro_skus')
    op.drop_column('maestro_skus', 'activo')
    op.drop_column('maestro_skus', 'id')

    op.drop_column('tenants_saas', 'estado')
    postgresql.ENUM(name='estadotenant').drop(bind, checkfirst=True)
    op.drop_column('plantas', 'activo')
    op.drop_column('plantas', 'timezone')

    op.drop_index('ix_lite_eventos_tenant_incluido_oee_timestamp', table_name='lite_eventos_produccion')
    op.drop_constraint('fk_lite_eventos_produccion_maquina_id', 'lite_eventos_produccion', type_='foreignkey')
    op.drop_index(op.f('ix_lite_eventos_produccion_event_id'), table_name='lite_eventos_produccion')
    op.drop_column('lite_eventos_produccion', 'incluido_oee')
    op.drop_column('lite_eventos_produccion', 'payload_hash')
    op.drop_column('lite_eventos_produccion', 'event_id')
    op.drop_column('lite_eventos_produccion', 'unidades_rechazadas')
    op.drop_column('lite_eventos_produccion', 'maquina_id')

    op.drop_column('dim_turnos', 'activo')
    op.drop_column('dim_supervisores', 'activo')
    op.drop_column('dim_operarios', 'activo')
    op.drop_column('dim_motivos_parada', 'activo')
    op.drop_column('dim_lineas', 'activo')
    op.drop_column('dim_lineas', 'metodo_calidad')
    postgresql.ENUM(name='metodocalidadlinea').drop(bind, checkfirst=True)

    op.drop_index(op.f('ix_maquina_estacion_tenant_id'), table_name='maquina_estacion')
    op.drop_table('maquina_estacion')
    op.drop_index(op.f('ix_api_keys_dispositivo_tenant_id'), table_name='api_keys_dispositivo')
    op.drop_index(op.f('ix_api_keys_dispositivo_key_id'), table_name='api_keys_dispositivo')
    op.drop_table('api_keys_dispositivo')
    op.drop_index(op.f('ix_usuario_planta_tenant_id'), table_name='usuario_planta')
    op.drop_table('usuario_planta')
    op.drop_index(op.f('ix_dim_maquinas_tenant_id'), table_name='dim_maquinas')
    op.drop_index(op.f('ix_dim_maquinas_codigo_externo'), table_name='dim_maquinas')
    op.drop_table('dim_maquinas')

    # NOTA: las filas de "Planta Default"/"Línea Default" creadas durante
    # el saneamiento de huérfanos NO se eliminan en el downgrade (podrían
    # tener líneas/estaciones reales asociadas si se siguió operando tras
    # el upgrade). Revisar manualmente antes de un rollback en un entorno
    # con datos reales.

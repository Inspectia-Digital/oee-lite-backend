import os
import sys
from sqlalchemy import create_engine, text
from sqlmodel import Session

# Añadimos la raíz al path para que resuelva los imports de 'app'
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.models.domain import Tenant, UsuarioSaaS, RolUsuario

def seed_core_system():
    """Siembra los datos fundacionales usando Pydantic para los defaults y Raw SQL para los ENUMs."""
    
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("❌ ERROR: DATABASE_URL no está definida. Abortando.")
        sys.exit(1)

    print("🔌 Conectando a la base de datos...")
    engine = create_engine(db_url)

    with Session(engine) as session:
        # ==========================================
        # 1. SEMBRAR TENANT MAESTRO
        # ==========================================
        print("🌱 Verificando Tenant Maestro...")
        tenant_exists = session.execute(text("SELECT id FROM tenants_saas WHERE id = 'tymeo_core'")).fetchone()
        
        if not tenant_exists:
            nuevo_tenant = Tenant(id="tymeo_core", nombre="InspectIA Core Admin")
            datos = nuevo_tenant.model_dump() if hasattr(nuevo_tenant, "model_dump") else nuevo_tenant.dict()
            
            # Enums en minúsculas para el Tenant
            datos["tipo"] = nuevo_tenant.tipo.value
            datos["modo_asignacion_operarios"] = nuevo_tenant.modo_asignacion_operarios.value

            sql = text("""
                INSERT INTO tenants_saas (
                    id, nombre, tipo, parent_id, modulos_contratados, theme_default,
                    logo_url, color_primario, locale_default, modo_asignacion_operarios,
                    activo, tolerancia_lento_pct, tolerancia_alerta_pct,
                    regex_parser_orden, regex_parser_sku, origen_maestros, created_at
                ) VALUES (
                    :id, :nombre, CAST(:tipo AS tipotenant), :parent_id, :modulos_contratados, :theme_default,
                    :logo_url, :color_primario, :locale_default, CAST(:modo_asignacion_operarios AS modoasignacionoperarios),
                    :activo, :tolerancia_lento_pct, :tolerancia_alerta_pct,
                    :regex_parser_orden, :regex_parser_sku, :origen_maestros, :created_at
                )
            """)
            session.execute(sql, datos)
            session.commit()
            print("✅ Tenant Maestro creado exitosamente.")
        else:
            print("⚡ Tenant Maestro ya existía.")

        # ==========================================
        # 2. SEMBRAR USUARIO SUPERADMIN
        # ==========================================
        print("👑 Verificando acceso SuperAdmin...")
        auth0_id = "google-oauth2|103641955647524616968"
        admin = session.execute(text("SELECT id FROM usuarios_saas WHERE auth0_id = :uid"), {"uid": auth0_id}).fetchone()
        
        if not admin:
            nuevo_admin = UsuarioSaaS(
                auth0_id=auth0_id,
                email="estanislao@inspectia.ai",
                tenant_id="tymeo_core",  
                rol=RolUsuario.SUPERADMIN,
                activo=True
            )
            datos_usr = nuevo_admin.model_dump() if hasattr(nuevo_admin, "model_dump") else nuevo_admin.dict()
            
            # CORRECCIÓN: Usamos .name para enviar "SUPERADMIN" en mayúsculas a Postgres
            datos_usr["rol"] = nuevo_admin.rol.name
            
            sql_usr = text("""
                INSERT INTO usuarios_saas (
                    id, auth0_id, tenant_id, email, rol, activo, nombre, apellido
                ) VALUES (
                    :id, :auth0_id, :tenant_id, :email, CAST(:rol AS rolusuario), :activo, :nombre, :apellido
                )
            """)
            session.execute(sql_usr, datos_usr)
            session.commit()
            print("✅ SuperAdmin fundacional creado exitosamente.")
        else:
            session.execute(text("""
                UPDATE usuarios_saas 
                SET rol = CAST('SUPERADMIN' AS rolusuario), tenant_id = 'tymeo_core' 
                WHERE auth0_id = :uid
            """), {"uid": auth0_id})
            session.commit()
            print("✅ Permisos de SuperAdmin actualizados.")

if __name__ == "__main__":
    print("🚀 Iniciando InspectIA System Seeder (Hybrid ORM/SQL Mode)...")
    seed_core_system()
    print("🏁 Proceso de sembrado finalizado.")
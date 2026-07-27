import os
import sys
from sqlalchemy import create_engine, text

def coronar_usuario_real():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("❌ ERROR: DATABASE_URL no está definida.")
        sys.exit(1)

    print("🔌 Conectando a la base de datos...")
    engine = create_engine(db_url)

    with engine.connect() as conn:
        # 1. Veamos qué IDs detectó la base de datos realmente
        usuarios = conn.execute(text("SELECT email, auth0_id, rol, tenant_id FROM usuarios_saas")).fetchall()
        print(f"👥 Usuarios detectados en tu sistema:")
        for u in usuarios:
            print(f"   - Email: {u[0]} | Auth0_ID: {u[1]} | Rol Actual: {u[2]} | Tenant: {u[3]}")

        # 2. Ascender a todos a SUPERADMIN y asignarlos al tenant maestro
        print("\n⚡ Aplicando el upgrade de permisos...")
        conn.execute(text("""
            UPDATE usuarios_saas 
            SET rol = 'SUPERADMIN', tenant_id = 'tymeo_core'
        """))
        conn.commit()
        print("✅ ¡Tu usuario real ahora es SuperAdmin con acceso a todos los módulos!")

if __name__ == "__main__":
    coronar_usuario_real()
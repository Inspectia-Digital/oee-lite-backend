import os
from sqlalchemy import create_engine, text

# Lee la variable de entorno que ya seteamos en la consola
db_url = os.environ.get("DATABASE_URL")

if not db_url:
    print("❌ ERROR: No se encontró la variable DATABASE_URL.")
    exit(1)

print(f"🔌 Conectando a {db_url.split('@')[1]}...")
engine = create_engine(db_url)

with engine.connect() as conn:
    print("🧹 Limpiando esquema public...")
    # Esto borra todas las tablas, relaciones y ENUMs de un plumazo
    conn.execute(text("DROP SCHEMA public CASCADE;"))
    conn.execute(text("CREATE SCHEMA public;"))
    conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
    conn.commit()

print("✅ Base de datos de Dev reiniciada. ¡Lista para Alembic!")
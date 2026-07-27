from sqlalchemy import text
from app.core.database import engine

def purgar_postgres():
    print("Iniciando purga de PostgreSQL...")
    try:
        # isolation_level="AUTOCOMMIT" es vital para borrar esquemas enteros
        with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE;"))
            conn.execute(text("CREATE SCHEMA public;"))
            conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
            print("✅ ¡Éxito! Esquema 'public' borrado y recreado.")
            print("Tu base de datos está completamente limpia.")
    except Exception as e:
        print(f"❌ Error al purgar: {e}")

if __name__ == "__main__":
    purgar_postgres()
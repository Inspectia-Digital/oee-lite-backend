from app.core.database import engine
from sqlmodel import Session, select
from app.models.domain import UsuarioSaaS, RolUsuario

# El ID exacto que leíste en el endpoint /me en Swagger
AUTH0_ID = "lvN3tWT4T1Zbi7qi0aAm7fdPZ5ZOCdfo@clients"

def restaurar_superadmin():
    with Session(engine) as session:
        usuario = session.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == AUTH0_ID)).first()
        
        if usuario:
            # Lo devolvemos al tenant maestro y le damos el rol máximo
            usuario.tenant_id = "tymeo_core"
            usuario.rol = RolUsuario.SUPERADMIN
            session.add(usuario)
            session.commit()
            print(f"✅ ¡Ascenso completado! El perfil {AUTH0_ID} ahora es SUPERADMIN en tymeo_core.")
        else:
            print("❌ No se encontró el usuario. Revisa el auth0_id.")

if __name__ == "__main__":
    restaurar_superadmin()
from sqlmodel import Session, select
from app.core.database import engine
from app.models.domain import UsuarioSaaS, RolUsuario, Tenant

def arreglar_usuario_terminal():
    print("\n🔧 Herramienta de Recuperación de Usuario de OEE Lite")
    email_swagger = input("👉 Ingresa el EMAIL EXACTO con el que inicias sesión en Swagger: ").strip()

    if not email_swagger:
        print("❌ Error: Debes ingresar un email válido.")
        return

    with Session(engine) as db:
        # 1. Asegurar que el tenant maestro existe
        inspectia = db.get(Tenant, "inspectia")
        if not inspectia:
            print("⚠️ Tenant 'inspectia' no encontrado. Creándolo automáticamente...")
            inspectia = Tenant(id="inspectia", nombre="InspectIA Admin", origen_maestros="MANUAL")
            db.add(inspectia)
            db.commit()

        # 2. Buscar al usuario en la base de datos
        usuario = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.email == email_swagger)).first()

        if usuario:
            print(f"ℹ️ El usuario '{email_swagger}' existe. Actualizando credenciales...")
            usuario.tenant_id = "inspectia"
            usuario.rol = RolUsuario.SUPERADMIN
            usuario.activo = True
            db.add(usuario)
        else:
            print(f"⚠️ El usuario no existe en BD local. Creándolo desde cero...")
            # Si usas Auth0, el middleware suele buscar por email si el auth0_id no coincide al inicio
            nuevo_usuario = UsuarioSaaS(
                auth0_id=f"auth0|mock_{email_swagger}",
                tenant_id="inspectia",
                email=email_swagger,
                rol=RolUsuario.SUPERADMIN,
                activo=True,
                nombre="Admin",
                apellido="Local"
            )
            db.add(nuevo_usuario)
        
        db.commit()
        print("\n✅ ¡Base de datos parcheada con éxito!")
        print(f"El usuario '{email_swagger}' ahora es SUPERADMIN de 'inspectia'.")
        print("Vuelve a disparar el endpoint en Swagger, el error de empresa asignada debería desaparecer.")

if __name__ == "__main__":
    arreglar_usuario_terminal()
import os

from sqlmodel import Session, select
from app.core.database import engine
from app.models.domain import Tenant, Planta, Linea, Estacion, TipoProduccion, UsuarioSaaS, RolUsuario

# Estructura base repetible (para resets de dev/local). El superadmin real
# se toma de variables de entorno, nunca hardcodeado en el código:
#   SUPERADMIN_AUTH0_ID, SUPERADMIN_EMAIL, SUPERADMIN_TENANT_ID (default "inspectia")
# Si no se setean, cae a un usuario simulado sólo apto para dev local puro
# (no permite login real contra Auth0).
SUPERADMIN_TENANT_ID = os.environ.get("SUPERADMIN_TENANT_ID", "inspectia")
SUPERADMIN_AUTH0_ID = os.environ.get("SUPERADMIN_AUTH0_ID", "auth0|inspectia_superadmin_dev")
SUPERADMIN_EMAIL = os.environ.get("SUPERADMIN_EMAIL", "admin@inspectia.com")


def seed_data():
    print("Iniciando sembrado de datos multi-tenant (InspectIA + Clientes)...")
    with Session(engine) as db:

        # ==========================================
        # 1. TENANT MASTER (Plataforma / Admin) + SuperAdmin real
        # ==========================================
        tenant_master = db.get(Tenant, SUPERADMIN_TENANT_ID)
        if not tenant_master:
            tenant_master = Tenant(
                id=SUPERADMIN_TENANT_ID,
                nombre="InspectIA Platform Admin",
                origen_maestros="MANUAL",
            )
            db.add(tenant_master)
            db.commit()
            print(f"✅ Tenant master '{SUPERADMIN_TENANT_ID}' creado.")
        else:
            print(f"ℹ️ Tenant master '{SUPERADMIN_TENANT_ID}' ya existe.")

        # Idempotente: crea o promueve al SuperAdmin, sin pisar otros usuarios.
        superadmin = db.exec(
            select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == SUPERADMIN_AUTH0_ID)
        ).first()
        if superadmin:
            superadmin.rol = RolUsuario.SUPERADMIN
            superadmin.tenant_id = SUPERADMIN_TENANT_ID
            superadmin.activo = True
            db.add(superadmin)
            db.commit()
            print(f"✅ Usuario SUPERADMIN ({SUPERADMIN_AUTH0_ID}) actualizado.")
        else:
            superadmin = UsuarioSaaS(
                auth0_id=SUPERADMIN_AUTH0_ID,
                tenant_id=SUPERADMIN_TENANT_ID,
                email=SUPERADMIN_EMAIL,
                rol=RolUsuario.SUPERADMIN,
                activo=True,
                nombre="Master",
                apellido="Architect"
            )
            db.add(superadmin)
            db.commit()
            print(f"✅ Usuario SUPERADMIN ({SUPERADMIN_AUTH0_ID}) creado.")

        # ==========================================
        # 2. CLIENTE 1: SPRINGWALL (Producción Discreta)
        # ==========================================
        sw_tenant = db.get(Tenant, "springwall")
        if not sw_tenant:
            sw_tenant = Tenant(
                id="springwall",
                nombre="Springwall S.A.",
                origen_maestros="MANUAL",
            )
            db.add(sw_tenant)
            db.commit()
            
            sw_planta = Planta(tenant_id="springwall", nombre="Planta Central Garín")
            db.add(sw_planta)
            db.commit()
            db.refresh(sw_planta)

            sw_linea = Linea(
                tenant_id="springwall", 
                planta_id=sw_planta.id, 
                nombre="Línea de Ensamble de Colchones A", 
                tipo_produccion=TipoProduccion.DISCRETA
            )
            db.add(sw_linea)
            db.commit()
            db.refresh(sw_linea)

            sw_estaciones = [
                Estacion(tenant_id="springwall", linea_id=sw_linea.id, nombre="E1 - Pedalera (Ingreso)", tipo="sensor", posicion_linea=1, codigo_plc="springwall_e1"),
                Estacion(tenant_id="springwall", linea_id=sw_linea.id, nombre="E2 - Matelaceado", tipo="sensor", posicion_linea=2, codigo_plc="springwall_e2"),
                Estacion(tenant_id="springwall", linea_id=sw_linea.id, nombre="E3 - Forro/Escaneo", tipo="escaneo_manual", posicion_linea=3, codigo_plc="springwall_e3")
            ]
            db.add_all(sw_estaciones)
            db.commit()
            print("✅ Tenant 'springwall' inicializado con configuración discreta histórica.")
        else:
            print("ℹ️ Tenant 'springwall' ya existe. Configuración preservada.")

        # ==========================================
        # 3. CLIENTE 2: GREEN MILLS / NEW GARDEN (Producción por Lotes)
        # ==========================================
        gm_tenant = db.get(Tenant, "green_mills")
        if not gm_tenant:
            gm_tenant = Tenant(
                id="green_mills",
                nombre="Green Mills S.A. (New Garden)",
                origen_maestros="MANUAL",
            )
            db.add(gm_tenant)
            db.commit()

            gm_planta = Planta(tenant_id="green_mills", nombre="Planta Alimentos Pilar")
            db.add(gm_planta)
            db.commit()
            db.refresh(gm_planta)

            gm_linea = Linea(
                tenant_id="green_mills", 
                planta_id=gm_planta.id, 
                nombre="Línea de Panificación Industrial 1", 
                tipo_produccion=TipoProduccion.POR_LOTES
            )
            db.add(gm_linea)
            db.commit()
            db.refresh(gm_linea)

            gm_estacion = Estacion(
                tenant_id="green_mills",
                linea_id=gm_linea.id,
                nombre="Empaquetadora Ciega (PLC)",
                tipo="sensor",
                codigo_plc="green_mills_empaquetadora_1",
                posicion_linea=1
            )
            db.add(gm_estacion)
            db.commit()
            print("✅ Tenant 'green_mills' (New Garden) creado con éxito (Producción por Lotes).")
        else:
            print("ℹ️ Tenant 'green_mills' ya existe.")

    print("🚀 Proceso de Seeding multi-tenant completo finalizado correctamente.")

if __name__ == "__main__":
    seed_data()

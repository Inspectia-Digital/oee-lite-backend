import os
from datetime import date
from decimal import Decimal

from sqlmodel import Session, select
from app.core.database import engine
from app.models.domain import (
    Tenant, Planta, Linea, Estacion, TipoProduccion, UsuarioSaaS, RolUsuario,
    ModuloDisponible, EstadoModuloDisponible,
    CaracteristicaModulo, PlanCaracteristica, PlanPrecio,
    MetodoPagoConfigurado, AsignacionModuloTenant,
)

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

        # ==========================================
        # 4. BILLING (Fase EB, PRD "Billing MVP" v2.0): catálogo real de
        # módulos -- mismos 5 códigos/estados que src/admin/modules.ts
        # (MODULE_CATALOG, frontend) tenía hardcodeados, para que la
        # migración a la tabla real (confirmado con el usuario) no
        # cambie de entrada lo que ve un tenant existente. Idempotente
        # (por código), igual que el resto de este script.
        # "activo" = enabled:true en MODULE_CATALOG; el resto quedan
        # "proximamente" (ninguno estaba en beta hoy).
        # ==========================================
        modulos_seed = [
            ("tymeo", "TYMEO", "OEE y Producción", 1, EstadoModuloDisponible.ACTIVO),
            ("oee-hub", "OEE Hub", "Factory OEE Hub", 2, EstadoModuloDisponible.PROXIMAMENTE),
            ("vision", "InspectIA Vision", "Visión artificial de calidad", 3, EstadoModuloDisponible.PROXIMAMENTE),
            ("logistica", "Logística", "Trazabilidad logística", 4, EstadoModuloDisponible.PROXIMAMENTE),
            ("seguridad", "Seguridad", "Seguridad y prevención", 5, EstadoModuloDisponible.PROXIMAMENTE),
        ]
        for codigo, nombre, descripcion, orden, estado in modulos_seed:
            existente = db.exec(select(ModuloDisponible).where(ModuloDisponible.codigo == codigo)).first()
            if existente:
                continue
            db.add(ModuloDisponible(
                codigo=codigo, nombre=nombre, descripcion=descripcion, orden=orden, estado=estado,
            ))
        db.commit()
        print("✅ Catálogo de módulos disponibles (Billing) sembrado.")

        # ==========================================
        # 5. SUBMÓDULOS DE TYMEO + PLAN COMPLETO (Fase FA.4.2, PRD
        # Segmentación de Planes). El acceso a pantallas se resuelve por
        # submódulo (CaracteristicaModulo -- ver su docstring: ES el
        # "submódulo" del PRD), con gate ESTRICTO: quien no lo tiene, no
        # lo ve. Por eso este bloque tiene que existir ANTES de que el
        # gate entre en vigor, o los tenants vivos perderían pantallas.
        #
        # NO se siembran Free/Start/Pro/Enterprise: esa estructura
        # comercial (y sus precios) es del próximo PRD, junto con los
        # formularios de OEE que la componen. "TYMEO Completo" es el
        # plan interno que representa acceso total, para los tenants que
        # ya venían operando.
        # ==========================================
        modulo_tymeo = db.exec(select(ModuloDisponible).where(ModuloDisponible.codigo == "tymeo")).first()
        if modulo_tymeo:
            submodulos_tymeo = [
                ("nucleo", "Núcleo", "Líneas, estaciones, máquinas, turnos, paradas y dashboard"),
                ("planificacion", "Planificación", "Planes de producción, órdenes y maestro de SKUs"),
                ("personas_rrhh", "Personas", "Operarios y supervisores de planta"),
                ("registro_manual", "Registro manual de OEE", "Carga de producción sin hardware conectado"),
            ]
            for codigo, nombre, descripcion in submodulos_tymeo:
                existente = db.exec(
                    select(CaracteristicaModulo).where(
                        CaracteristicaModulo.modulo_id == modulo_tymeo.id,
                        CaracteristicaModulo.codigo == codigo,
                    )
                ).first()
                if not existente:
                    db.add(CaracteristicaModulo(
                        modulo_id=modulo_tymeo.id, codigo=codigo, nombre=nombre, descripcion=descripcion,
                    ))
            db.commit()

            plan_completo = db.exec(
                select(PlanPrecio).where(
                    PlanPrecio.modulo_id == modulo_tymeo.id, PlanPrecio.codigo == "completo",
                )
            ).first()
            if not plan_completo:
                plan_completo = PlanPrecio(
                    modulo_id=modulo_tymeo.id, codigo="completo", nombre="TYMEO Completo",
                    descripcion="Acceso a todos los submódulos. Plan interno, no un tier comercial.",
                    # Precio 0: no representa una tarifa real, es el plan
                    # de los tenants que ya operaban antes de que
                    # existiera la estructura comercial. Los precios
                    # reales llegan con el próximo PRD.
                    precio=Decimal("0.00"), orden=99,
                )
                db.add(plan_completo)
                db.commit()
                db.refresh(plan_completo)

            # Todos los submódulos van a este plan (es el "completo").
            for caracteristica in db.exec(
                select(CaracteristicaModulo).where(CaracteristicaModulo.modulo_id == modulo_tymeo.id)
            ).all():
                ya_incluida = db.exec(
                    select(PlanCaracteristica).where(
                        PlanCaracteristica.plan_id == plan_completo.id,
                        PlanCaracteristica.caracteristica_id == caracteristica.id,
                    )
                ).first()
                if not ya_incluida:
                    db.add(PlanCaracteristica(plan_id=plan_completo.id, caracteristica_id=caracteristica.id))
            db.commit()
            print("✅ Submódulos de TYMEO + plan 'TYMEO Completo' sembrados.")

            # Método de pago: AsignacionModuloTenant.metodo_pago_id es
            # NOT NULL, así que sin al menos uno no se puede asignar
            # ningún plan a nadie. Se siembra genérico y sin datos
            # bancarios inventados -- el detalle real lo carga el
            # SuperAdmin desde el panel.
            metodo_pago = db.exec(select(MetodoPagoConfigurado).where(MetodoPagoConfigurado.codigo == "a_convenir")).first()
            if not metodo_pago:
                metodo_pago = MetodoPagoConfigurado(
                    codigo="a_convenir", nombre="A convenir", tipo="transferencia",
                    detalle=None, orden=1,
                )
                db.add(metodo_pago)
                db.commit()
                db.refresh(metodo_pago)

            # Asignación a los tenants que YA operaban: sin esto pierden
            # las pantallas nuevas cuando el gate estricto entre en
            # vigor (ver submodulos_efectivos_tenant, app/core/planes.py).
            hoy = date.today()
            for tenant_id in ("green_mills", "springwall"):
                if not db.get(Tenant, tenant_id):
                    continue
                ya_asignado = db.exec(
                    select(AsignacionModuloTenant).where(
                        AsignacionModuloTenant.tenant_id == tenant_id,
                        AsignacionModuloTenant.plan_id == plan_completo.id,
                    )
                ).first()
                if ya_asignado:
                    continue
                db.add(AsignacionModuloTenant(
                    tenant_id=tenant_id, modulo_id=modulo_tymeo.id, plan_id=plan_completo.id,
                    metodo_pago_id=metodo_pago.id, fecha_inicio=hoy,
                    fecha_renovacion=hoy.replace(year=hoy.year + 1),
                    precio_base=Decimal("0.00"), precio_con_descuento=Decimal("0.00"),
                ))
            db.commit()
            print("✅ TYMEO Completo asignado a los tenants existentes.")

    print("🚀 Proceso de Seeding multi-tenant completo finalizado correctamente.")

if __name__ == "__main__":
    seed_data()

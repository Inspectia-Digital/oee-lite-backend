import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select, SQLModel

from app.core.database import get_session, engine
from app.core.auth import verificar_token_auth0, get_usuario_actual
from app.models.domain import UsuarioSaaS, RolUsuario

# ==========================================
# IMPORTACIÓN DE ROUTERS
# ==========================================
# Routers migrados al nuevo estándar de InspectIA OS
from app.routers import scans, operacion, importaciones

# Routers legacy que aún mantienen su ubicación original
from app.routers import analytics, configuracion, admin, jerarquia 

# ==========================================
# GESTIÓN DEL CICLO DE VIDA (CLOUD RUN)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Controla el arranque y apagado del contenedor en Google Cloud Run.
    Evita el bloqueo de hilos reemplazando la llamada global antigua[cite: 7].
    """
    # Escanea tus modelos y crea las tablas que falten en Postgres
    SQLModel.metadata.create_all(engine)
    yield
    # (Espacio reservado para cerrar conexiones de base de datos o Redis de forma segura)

# ==========================================
# INICIALIZACIÓN DE LA APLICACIÓN
# ==========================================
app = FastAPI(
    title="InspectIA OS - Backend Core",
    description="API B2B Multi-Tenant y Multi-Planta orientada a eventos para manufactura.",
    version="2.0.0",
    lifespan=lifespan
)

# ==========================================
# SEGURIDAD CORS (VITAL PARA EL FRONTEND)
# ==========================================
origins = [
    "http://localhost:5173",       
    "http://localhost:8080",       
    "https://*.lovable.app",       
    "*"  # Permitido temporalmente para desarrollo. Restringir a dominios exactos en Prod.
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["*"],
    # Exponemos explícitamente el header de la Planta para el OS Shell
    allow_headers=["*", "Authorization", "X-Sub-Tenant-Id"],
    expose_headers=["X-Sub-Tenant-Id"]
)

# ==========================================
# REGISTRO DE MÓDULOS (ROUTERS)
# ==========================================

# 1. Edge API (Terminales Kiosko y PLCs)
app.include_router(scans.router)

# 2. UI Supervisor (Protegida por Planta)
app.include_router(operacion.router)

# 3. Cargas Masivas (Excel/CSV protegidas por Planta)
app.include_router(importaciones.router)

# 4. Módulos Heredados (Tymeo)[cite: 7]
app.include_router(admin.router)
app.include_router(configuracion.router)
app.include_router(analytics.router)
app.include_router(jerarquia.router)


# ==========================================
# ENDPOINTS BASE (INFRAESTRUCTURA)
# ==========================================
@app.get("/", tags=["Infraestructura"])
def read_root():
    """Mantenido por retrocompatibilidad[cite: 7]"""
    return {"message": "InspectIA OS API is running"}

@app.get("/health", tags=["Infraestructura"])
def health_check():
    """Endpoint vital para el Load Balancer de Google Cloud Run[cite: 7]"""
    return {
        "status": "ok", 
        "version": "2.0.0", 
        "mensaje": "¡El motor de InspectIA OS está encendido y refactorizado!"
    }


# ==========================================
# ENDPOINTS DE EMERGENCIA / BOOTSTRAP[cite: 7]
# CTO Warning: Asegurar de deshabilitar esto en producción estable.
# ==========================================
@app.get("/ruta-secreta", tags=["Emergencia"])
def ver_secreto(usuario_validado: dict = Depends(get_usuario_actual)):
    return {
        "mensaje": "¡Entraste a la bóveda de InspectIA OS!",
        "datos_del_token": usuario_validado
    }

@app.get("/ascender-estanislao", tags=["Emergencia"])
def ascender_estanislao(db: Session = Depends(get_session)):
    """Ruta temporal de emergencia para ascender o CREAR al usuario de Google OAuth[cite: 7]"""
    id_google = "google-oauth2|103641955647524616968"
    
    mi_usuario = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == id_google)).first()
    
    if mi_usuario:
        mi_usuario.rol = RolUsuario.SUPERADMIN
        mi_usuario.tenant_id = "tymeo_core"
        db.add(mi_usuario)
        db.commit()
        return {"status": "ÉXITO", "mensaje": "¡Usuario actualizado a SUPERADMIN!"}
    else:
        nuevo_admin = UsuarioSaaS(
            auth0_id=id_google,
            email="estanislao@inspectia.ai",
            tenant_id="tymeo_core",  
            rol=RolUsuario.SUPERADMIN,
            activo=True
        )
        db.add(nuevo_admin)
        db.commit()
        return {"status": "ÉXITO", "mensaje": "¡Usuario CREADO desde cero y coronado como SUPERADMIN!"}

@app.post("/setup/primer-admin", tags=["Emergencia"])
def crear_primer_superadmin(
    payload: dict = Depends(verificar_token_auth0), 
    db: Session = Depends(get_session)
):
    """Ruta temporal: Registra el token de Auth0 actual como SUPERADMIN[cite: 7]."""
    auth0_sub = payload.get("sub")
    
    usuario_existente = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    if usuario_existente:
        return {"mensaje": "Ya estás registrado en la base de datos.", "usuario": usuario_existente}
        
    nuevo_admin = UsuarioSaaS(
        auth0_id=auth0_sub,
        tenant_id="tymeo_core",  
        rol=RolUsuario.SUPERADMIN
    )
    
    db.add(nuevo_admin)
    db.commit()
    db.refresh(nuevo_admin)
    
    return {
        "mensaje": "¡Nacimiento de InspectIA OS exitoso! Has sido coronado como SuperAdmin.",
        "usuario": nuevo_admin
    }
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlmodel import SQLModel

from app.core.config import settings
from app.core.database import engine

# ==========================================
# IMPORTACIÓN DE ROUTERS
# ==========================================
# Routers migrados al nuevo estándar de InspectIA OS
from app.routers import scans, operacion, importaciones

# Routers legacy que aún mantienen su ubicación original
from app.routers import analytics, configuracion, admin, jerarquia, plantas

# Fase D — M2M y RBAC geolocalizado
from app.routers import dispositivos

# Fase API/CRUD completo
from app.routers import personas


# ==========================================
# GESTIÓN DEL CICLO DE VIDA (CLOUD RUN)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Controla el arranque y apagado del contenedor en Google Cloud Run.
    AUTO_CREATE_TABLES sólo debe usarse en desarrollo descartable; el esquema
    real se gestiona con Alembic (ver alembic/).
    """
    if settings.AUTO_CREATE_TABLES:
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
# SEGURIDAD CORS
# ==========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
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

# 4. Módulos Heredados (Tymeo)
app.include_router(admin.router)
app.include_router(configuracion.router)
app.include_router(analytics.router)
app.include_router(jerarquia.router)
app.include_router(plantas.router)

# 5. Fase D — M2M y RBAC geolocalizado
app.include_router(dispositivos.router)

# 6. Fase API/CRUD completo
app.include_router(personas.router)


# ==========================================
# ENDPOINTS BASE (INFRAESTRUCTURA)
# ==========================================
@app.get("/", tags=["Infraestructura"])
def read_root():
    """Mantenido por retrocompatibilidad"""
    return {"message": "InspectIA OS API is running"}


@app.get("/health/live", tags=["Infraestructura"])
def health_live():
    """Liveness: sólo confirma que el proceso responde HTTP, sin dependencias externas."""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/health/ready", tags=["Infraestructura"])
def health_ready():
    """Readiness: confirma que la app puede atender tráfico real (DB accesible)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    return {"status": "ok", "version": "2.0.0"}


@app.get("/health", tags=["Infraestructura"])
def health_check_alias():
    """Alias temporal de compatibilidad; equivale a /health/ready."""
    return health_ready()

# ==========================================
# NOTA: se eliminaron las rutas de emergencia/bootstrap
# (/ruta-secreta, /ascender-estanislao, /setup/primer-admin).
# El bootstrap de un SuperAdmin local se hace ahora fuera del repo,
# ver InspectIA/scripts-emergencia-fuera-de-repo/bootstrap_superadmin.py
# ==========================================

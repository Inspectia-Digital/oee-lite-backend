import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import SQLModel

from app.core.config import settings
from app.core.database import engine
from app.core.observabilidad import RequestIDMiddleware, obtener_request_id_actual

logger = logging.getLogger(__name__)

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

# Fase S — recómputo de eventos ya ingeridos
from app.routers import recomputo

# Fase EB — Billing MVP (catálogo de módulos/planes/métodos de pago)
from app.routers import billing


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
    # Fase CA: X-Error-Code (app/core/errors.py) es CORS por naturaleza --
    # el gateway/terminal de Green Mills corre en otro origen que el
    # backend. Sin listarlo acá, el browser lo recibe pero JS no puede
    # leerlo (regla de CORS: headers de respuesta no listados en
    # Access-Control-Expose-Headers son invisibles para fetch/XHR aunque
    # viajen igual por la red) -- el frontend habría quedado leyendo
    # `undefined` en TODOS los casos sin este agregado.
    expose_headers=["X-Sub-Tenant-Id", "X-Request-Id", "X-Error-Code"]
)

# Observabilidad mínima (Fase K, auditoría QA #17): request-id correlacionable
# + log de acceso estructurado por request. Se agrega último para que quede
# como capa más externa y mida la duración total real del request.
app.add_middleware(RequestIDMiddleware)


# ==========================================
# MANEJO GLOBAL DE ERRORES (Fase AT, auditoría QA, QA-16)
# ==========================================
@app.exception_handler(Exception)
async def manejador_global_de_excepciones(request: Request, exc: Exception) -> JSONResponse:
    """QA-16 (auditoría QA): varios endpoints de analytics.py capturaban
    cualquier excepción y devolvían [] silenciosamente en vez de dejarla
    propagar -- el frontend no podía distinguir "sin datos" de "error
    real" (withPreviewFallback, en previewMode.ts, SÍ está pensado para
    marcar la feature como "pendiente de backend" ante un error HTTP
    real, pero nunca se disparaba porque el backend nunca fallaba
    visiblemente). Ese fix (sacar los `except Exception: return []`
    locales) exige que exista una red de contención acá: sin esto,
    cualquier excepción no prevista llegaría cruda hasta
    Starlette (texto plano "Internal Server Error", sin el sobre JSON
    {"detail": ...} que usa el resto de esta API) o, peor, filtraría el
    traceback si algún día se corre con debug=True.

    Nunca se expone el mensaje de la excepción ni el traceback al
    cliente -- se loguean completos server-side (correlacionados por
    request_id, ver RequestIDMiddleware) y el cliente sólo recibe un 500
    genérico con ese mismo request_id para poder reportarlo."""
    request_id = obtener_request_id_actual()
    logger.error(
        "Excepción no manejada: method=%s path=%s request_id=%s",
        request.method, request.url.path, request_id,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Error interno del servidor. Si persiste, contactá a soporte con el request_id.",
            "request_id": request_id,
        },
    )

# ==========================================
# REGISTRO DE MÓDULOS (ROUTERS)
# ==========================================

# 1. Edge API (Terminales Kiosko y PLCs)
app.include_router(scans.router)

# 2. UI Supervisor (Protegida por Planta)
app.include_router(operacion.router)
app.include_router(operacion.router_asignaciones_supervisor)

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

# 7. Fase S — recómputo de eventos ya ingeridos
app.include_router(recomputo.router)

# 8. Fase EB — Billing MVP
app.include_router(billing.router)


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

import logging
from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    PROJECT_NAME: str = "OEE Lite API"
    VERSION: str = "1.0.0"

    # "development" | "staging" | "production"
    ENVIRONMENT: str = "development"

    DATABASE_URL: str
    DATABASE_ECHO: bool = False
    AUTO_CREATE_TABLES: bool = False

    AUTH0_DOMAIN: str = ""
    AUTH0_AUDIENCE: str = ""
    AUTH0_JWKS_TIMEOUT_SECONDS: float = 5.0

    # App M2M separada, autorizada contra la Management API (scope
    # create:user_tickets) para el reset de password (Fase J). Opcional: si
    # falta, el endpoint devuelve 503 en vez de romper el arranque -- no es
    # requerida para el resto de la app.
    AUTH0_MGMT_CLIENT_ID: str = ""
    AUTH0_MGMT_CLIENT_SECRET: str = ""
    AUTH0_MGMT_TIMEOUT_SECONDS: float = 5.0

    # Lista separada por comas, ej: "https://app.midominio.com,https://admin.midominio.com"
    CORS_ORIGINS: str = ""

    class Config:
        env_file = ".env"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def cors_origins_list(self) -> List[str]:
        origenes = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        if not origenes and not self.is_production:
            # Fallback de transición: si CORS_ORIGINS no está seteado fuera de
            # producción, no bloquear silenciosamente al frontend. En producción
            # esto sigue siendo obligatorio (ver _validar_configuracion_de_produccion).
            logger.warning(
                "CORS_ORIGINS no está seteado como variable de entorno; "
                "permitiendo todos los orígenes ('*') como fallback de transición. "
                "Configurar explícitamente antes de promover a producción."
            )
            return ["*"]
        return origenes

    @field_validator("ENVIRONMENT")
    @classmethod
    def _normalizar_environment(cls, v: str) -> str:
        v_normalizado = v.strip().lower()
        if v_normalizado not in {"development", "staging", "production"}:
            raise ValueError(
                "ENVIRONMENT debe ser 'development', 'staging' o 'production'"
            )
        return v_normalizado

    @model_validator(mode="after")
    def _validar_configuracion_de_produccion(self) -> "Settings":
        if not self.is_production:
            return self

        errores = []
        if not self.cors_origins_list:
            errores.append("CORS_ORIGINS no puede estar vacío en producción.")
        if "*" in self.cors_origins_list:
            errores.append("CORS_ORIGINS no puede incluir '*' en producción.")
        if self.DATABASE_ECHO:
            errores.append("DATABASE_ECHO debe ser false en producción.")
        if self.AUTO_CREATE_TABLES:
            errores.append("AUTO_CREATE_TABLES debe ser false en producción (usar Alembic).")
        if not self.AUTH0_DOMAIN:
            errores.append("AUTH0_DOMAIN es obligatorio en producción.")
        if not self.AUTH0_AUDIENCE:
            errores.append("AUTH0_AUDIENCE es obligatorio en producción.")

        if errores:
            raise ValueError(
                "Configuración de producción inválida:\n- " + "\n- ".join(errores)
            )
        return self


settings = Settings()

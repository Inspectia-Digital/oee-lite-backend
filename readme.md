# OEE Lite Backend (Tymeo)

Backend del módulo **Tymeo** (OEE en tiempo real, sensores/PLC/terminales de
escaneo) dentro de la plataforma InspectIA OS. Multi-tenant B2B: cada
empresa cliente (`Tenant`) tiene N plantas físicas, cada planta N líneas,
cada línea N estaciones. Ingiere eventos de producción desde hardware
(PLC vía M2M) o terminales de escaneo, calcula OEE (Disponibilidad ×
Rendimiento × Calidad) y expone la configuración/analítica vía API REST.

No incluye: Factory OEE Hub (`/hub/*`, carga manual/batch de OEE) — es un
producto aparte, con su propio modelo de datos, fuera de este repo.

## Stack

- **Python 3.11**, **FastAPI**, **SQLModel** (sobre SQLAlchemy 2.x).
- **PostgreSQL 15**, migraciones con **Alembic** (SQL explícito para
  índices parciales, CHECK constraints y triggers; no se delega en
  autogenerate sin revisión manual).
- **Auth0** (JWT humano) + API Keys propias (`X-Device-Key`) para
  dispositivos M2M (PLC/terminales).
- **pytest** contra Postgres real (nunca mocks para RBAC/triggers/reglas
  de negocio) — ver `tests/`.
- Despliegue en **Google Cloud Run** + Cloud SQL, CI/CD en GitHub Actions.

## Arquitectura (resumen)

```
app/
  core/        # config, DB, auth humana (Auth0) y M2M (API keys), RBAC,
               # observabilidad (request-id), cliente Auth0 Management API
  models/      # SQLModel: Tenant, Planta, Linea, Estacion, eventos,
               # paradas, permisos por módulo, etc.
  routers/     # endpoints REST: scans (ingesta Edge), operacion
               # (supervisor), configuracion (CRUD maestros), analytics,
               # admin (accesos/RBAC), plantas, dispositivos, personas
alembic/       # migraciones SQL-first, numeradas y comentadas
tests/         # pytest, requiere Postgres real (docker-compose)
docs/          # notas de fase, contrato para el frontend, runbooks
```

Convenciones: soft-delete en toda entidad de negocio (`activo`/`activa`,
nunca `DELETE` físico salvo excepciones documentadas puntuales), RBAC
geolocalizado (`UsuarioPlanta`/`ModuloPermiso` para Supervisor/Operario;
Gerencia/SuperAdmin/Producción ven todo el tenant), idempotencia de
ingesta por `event_id`.

## Cómo correr en local

Requiere Docker (Postgres) y Python 3.11+.

```bash
# 1. Levantar Postgres local
docker compose up -d db

# 2. Copiar variables de entorno
cp .env.example .env
# Completar AUTH0_DOMAIN/AUTH0_AUDIENCE si vas a probar login humano real.
# El puerto de Postgres en docker-compose es 5433 (no 5432), a propósito
# para no chocar con un Cloud SQL Auth Proxy corriendo en la misma máquina.

# 3. Entorno virtual + dependencias (incluye deps de test)
python -m venv venv
venv/Scripts/activate  # Windows; en Linux/Mac: source venv/bin/activate
pip install -r requirements-dev.txt

# 4. Migraciones
alembic upgrade head

# 5. (Opcional) Seed de datos base para desarrollo
python seed.py

# 6. Levantar la API
uvicorn main:app --reload --port 8080
# Swagger UI en http://localhost:8080/docs

# 7. Tests (requiere el Postgres del paso 1 arriba y corriendo)
pytest tests/ -v
```

## Documentación adicional

- [`docs/CAMBIOS_FRONTEND_REQUERIDOS.md`](docs/CAMBIOS_FRONTEND_REQUERIDOS.md)
  y [`docs/CAMBIOS_FRONTEND_FASES_F_J.md`](docs/CAMBIOS_FRONTEND_FASES_F_J.md)
  — contrato de API para el equipo de frontend (InspectIA OS), endpoint
  por endpoint, con lo que cambió de un contrato anterior.
- [`docs/COMANDOS_PRODUCTION_GRADE.md`](docs/COMANDOS_PRODUCTION_GRADE.md)
  — comandos de referencia (migraciones, tests, rollback).
- [`docs/ROLLBACK_PRODUCTION_GRADE.md`](docs/ROLLBACK_PRODUCTION_GRADE.md)
  — cómo revertir una migración o un despliegue.
- OpenAPI interactivo: `/docs` (Swagger) y `/redoc` en cualquier ambiente
  desplegado.

## CI/CD

`.github/workflows/deploy-dev.yml`: push a `dev` corre primero el suite de
tests completo contra un Postgres real (job `test`); el despliegue a Cloud
Run (job `deploy`) sólo arranca si los tests pasan. El entorno de
producción (Cloud Run + Cloud SQL + workflow separados) se documenta en
`docs/` a medida que se arma (ver fase L del hardening).

## Seguridad

- Nunca commitear `.env` ni secretos (`.gitignore` ya excluye `.env*`
  salvo `.env.example`).
- Credenciales de terceros (Auth0 Management API, etc.) van como secretos
  del CI/Cloud Run, nunca hardcodeadas en el código.
- El aislamiento multi-tenant es a nivel de aplicación (cada query filtra
  explícitamente por `tenant_id`; no hay Row-Level Security de Postgres
  activada) — ver `tests/test_tenant_isolation.py`.

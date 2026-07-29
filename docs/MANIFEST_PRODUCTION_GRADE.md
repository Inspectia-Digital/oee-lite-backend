# Manifiesto — hardening/production-grade

Base: `main` @ `2579d39` (fix: stabilize imports and dependencies for clean cloud run startup)
Rama: `hardening/production-grade`
Commits incluidos (en orden):

1. `a023fb0` security: remove exposed credentials scripts and bootstrap routes (Fase A)
2. `2a77c05` feat(models): add production-grade tenant-aware domain (Fase B1)
3. `c6e2439` feat(migrations): add expand migration and deterministic defaults (Fase C1)
4. `c72b862` fix(auth): keep transitional Auth0 fallback outside production
5. `dd7c917` fix(config): keep permissive CORS fallback outside production

## Archivos agregados

- `.dockerignore`
- `.env.example`
- `alembic/versions/d40754a1ed06_expand_production_grade_c1.py`
- `docs/PR_PRODUCTION_GRADE.md`
- `docs/MANIFEST_PRODUCTION_GRADE.md`
- `docs/COMANDOS_PRODUCTION_GRADE.md`
- `docs/ROLLBACK_PRODUCTION_GRADE.md`

## Archivos modificados

- `.gitignore` — permite `.env.example` pese al patrón `.env.*`.
- `app/core/auth.py` — settings centralizado, timeout JWKS, errores sanitizados, fallback Auth0 sólo fuera de producción.
- `app/core/config.py` — `Settings` centralizado (pydantic-settings) con validación estricta de producción; fallback CORS sólo fuera de producción.
- `app/core/database.py` — `DATABASE_ECHO` configurable (default false), `pool_pre_ping=True`.
- `app/models/domain.py` — ver detalle de modelo en Fase B1/C1 (nuevas tablas, columnas `activo`, identidad UUID de SKU/Orden, snapshot `incluido_oee`, fix de enums `modo_asignacion_operarios`).
- `docker-compose.yml` — Postgres local en `127.0.0.1:5433` (no 5432, choca con `cloud-sql-proxy.exe` local) + healthcheck.
- `main.py` — sin `create_all()` incondicional, sin rutas de emergencia, `/health/live` + `/health/ready`, CORS desde settings.
- `requirements.txt` — normalizado de UTF-16 con BOM a UTF-8 sin BOM; suma `httpx==0.27.2`, `pytest==9.1.1`.

## Archivos eliminados del repo (movidos fuera, ver docs/PR_PRODUCTION_GRADE.md)

- `fix_admin.py`, `fix_user.py`, `seed_system.py`, `setup_cli.py` — hardcodeaban un `auth0_id` personal y ascendían usuarios a SUPERADMIN por SQL directo sin RBAC (hallazgo SEC-02). Movidos a `InspectIA/scripts-emergencia-fuera-de-repo/` (fuera del repo). Reemplazados por `bootstrap_superadmin.py` parametrizado (sin hardcodear IDs), en la misma carpeta externa.
- `nuevo_main.py`, `v1_main.py` — entrypoints muertos sin referencias en Dockerfile/CI/otros módulos.

## Archivos NO tocados (a propósito, pendientes)

- `auth.txt` — credencial en texto plano, sigue trackeada en git. Requiere rotación y decisión sobre limpieza de historial (fuera del alcance de este PR).
- `cloud-sql-proxy.exe` (binario ~35MB) — sigue trackeado en git.
- `reset_db.py`, `seed.py` — utilidades de dev legítimas, sin secretos ni escalamiento de rol, se mantienen.

## Migraciones

- Head anterior (main): `9261c6f3fe42`
- Head nuevo (esta rama): `d40754a1ed06` (revisa a `9261c6f3fe42`)
- Estrategia: **expand pura**. No retira columnas ni tablas legacy. `codigo_sku`/`id_orden` siguen como PK hasta la fase contract (C2, no incluida en este PR).

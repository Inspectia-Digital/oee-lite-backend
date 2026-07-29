# Manifiesto — hardening/production-grade

Base: `main` @ `2579d39` (fix: stabilize imports and dependencies for clean cloud run startup)
Estado actual: `main` @ `643a3f6` (test: add production-grade regression suite)
Todo commiteado directamente a `main` local (sin rama intermedia ni PR — flujo acordado con el usuario). El PR real hacia `dev` lo abre el usuario manualmente.

## Commits incluidos (orden cronológico)

| # | Commit | Fase |
|---|---|---|
| 1 | `a023fb0` security: remove exposed credentials scripts and bootstrap routes | A |
| 2 | `2a77c05` feat(models): add production-grade tenant-aware domain | B1 |
| 3 | `c6e2439` feat(migrations): add expand migration and deterministic defaults | C1 |
| 4 | `c72b862` fix(auth): keep transitional Auth0 fallback outside production | A (fix) |
| 5 | `dd7c917` fix(config): keep permissive CORS fallback outside production | A (fix) |
| 6 | `10362c7` docs: add production-grade PR description, manifest, commands and rollback | docs |
| 7 | `75cdf59` feat(ci): automate dev migration and add repeatable base seed | CI |
| 8 | `ba39972` fix(ci): use official Cloud SQL Auth Proxy action instead of manual script | CI (fix, incorrecto) |
| 9 | `3d4eeb2` fix(ci): revert to official cloud-sql-proxy binary, fix background lifetime | CI (fix real) |
| 10 | `509d2a7` feat(auth): add M2M API keys and plant-scoped user assignment CRUD | D.1 |
| 11 | `1280986` feat(auth): implement real tenant suspension via Tenant.estado | D.2 |
| 12 | `65e49ba` feat(rbac): enforce plant-scoped access for Supervisor/Operario | D.3 |
| 13 | `abc32ec` feat(edge): add real M2M authentication for device ingestion | D.4a |
| 14 | `f56feaf` feat(edge): add operator scan-login writing to AsignacionTurno | D.4b |
| 15 | `232f623` feat(edge): add idempotency, timezone normalization and machine handling | E1 |
| 16 | `8eae466` feat(oee): correct performance/availability/quality formulas | E2 |
| 17 | `0d27370` feat(api): complete tenant-safe soft-delete CRUD for physical hierarchy | API/CRUD |
| 18 | `9bf4e6c` feat(api): add Operario and Supervisor CRUD (didn't exist before) | API/CRUD |
| 19 | `f216a36` feat(api): complete OrdenProduccion CRUD, SKU/Tenant/Usuario detail endpoints | API/CRUD |
| 20 | `643a3f6` test: add production-grade regression suite | tests |

## Archivos agregados

- `.dockerignore`, `.env.example`
- `alembic/versions/d40754a1ed06_expand_production_grade_c1.py` (Fase C1)
- `alembic/versions/c941d511ae50_d1_activo_usuario_planta.py` (Fase D.1)
- `alembic/versions/3dfb6eb20b53_e2_tiempo_ideal_seg.py` (Fase E2)
- `app/core/auth_m2m.py` — autenticación M2M para dispositivos (Fase D.4a)
- `app/core/rbac.py` — dependencia RBAC reutilizable (`requerir_roles`)
- `app/routers/dispositivos.py` — CRUD de `ApiKeyDispositivo` (Fase D.1)
- `app/routers/personas.py` — CRUD de Operario y Supervisor (no existía)
- `pytest.ini`
- `tests/` completo: `conftest.py` + 5 archivos de tests (44 tests)
- `docs/PR_PRODUCTION_GRADE.md`, `docs/MANIFEST_PRODUCTION_GRADE.md`, `docs/COMANDOS_PRODUCTION_GRADE.md`, `docs/ROLLBACK_PRODUCTION_GRADE.md`, `docs/CAMBIOS_FRONTEND_REQUERIDOS.md`

## Archivos modificados

- `.github/workflows/deploy-dev.yml` — migración automática contra Cloud SQL antes del deploy (Cloud SQL Auth Proxy oficial descargado directo, no una GitHub Action inexistente).
- `.gitignore` — permite `.env.example`.
- `app/core/auth.py` — settings centralizado, timeout JWKS, errores sanitizados, fallback Auth0 fuera de producción, dependencias `obtener_contexto_tenant_humano`/`_edge`, suspensión de tenant.
- `app/core/config.py` — `Settings` centralizado con validación estricta de producción; fallback CORS fuera de producción.
- `app/core/database.py` — `DATABASE_ECHO` configurable, `pool_pre_ping=True`.
- `app/models/domain.py` — modelo completo de Fase B1/C1/D.1/E2 (ver PR_PRODUCTION_GRADE.md).
- `app/routers/admin.py` — suspensión de tenant, CRUD `UsuarioPlanta`, RBAC geolocalizado en listados, detalle de tenants/usuarios (global y por tenant), fix de hard-delete.
- `app/routers/analytics.py` — motor OEE corregido (Fase E2).
- `app/routers/configuracion.py` — CRUD completo de Línea/Estación/Motivo/Turno/Orden, fix de hard-delete en Estación.
- `app/routers/importaciones.py`, `app/routers/plantas.py` — migran a `obtener_contexto_tenant_humano`; Planta gana RBAC en creación + CRUD completo.
- `app/routers/jerarquia.py` — **bug real corregido**: `id: int` en vez de UUID, rompía cualquier llamada real.
- `app/routers/operacion.py` — RBAC geolocalizado real (antes sólo chequeaba que hubiera un header, no la asignación); endpoint de asignación retroactiva deshabilitado (410, nunca persistía nada).
- `app/routers/scans.py` — M2M real, idempotencia, timezone, snapshot `incluido_oee`/`tiempo_ideal_seg`, login de operario por escaneo.
- `docker-compose.yml` — Postgres en `127.0.0.1:5433` (no 5432, choca con `cloud-sql-proxy.exe` local) + healthcheck.
- `main.py` — sin `create_all()` incondicional, sin rutas de emergencia, `/health/live` + `/health/ready`, router `personas` registrado.
- `requirements.txt` — normalizado UTF-16→UTF-8, suma `httpx`, `pytest`, `bcrypt`.
- `seed.py` — superadmin parametrizable por variables de entorno (sin hardcodear `auth0_id`).

## Archivos eliminados del repo (movidos fuera)

- `fix_admin.py`, `fix_user.py`, `seed_system.py`, `setup_cli.py` — hardcodeaban un `auth0_id` personal y ascendían usuarios a SUPERADMIN por SQL directo (SEC-02). Movidos a `InspectIA/scripts-emergencia-fuera-de-repo/`. Reemplazados por `bootstrap_superadmin.py` parametrizado.
- `nuevo_main.py`, `v1_main.py` — entrypoints muertos sin referencias.

## Código muerto identificado, no tocado (documentado, no resucitado ni borrado)

- `app/api/plc_router.py`, `app/api/lite_router.py`, `app/routers/erp.py` — **no están registrados en `main.py`**, inalcanzables. Se decidió con el usuario mantener un solo pipeline de ingesta (`scans.py`) en vez de resucitarlos; quedan como candidatos a limpieza en un PR aparte.

## Archivos NO tocados (a propósito, pendientes)

- `auth.txt` — credencial en texto plano, sigue trackeada en git. Requiere rotación y decisión sobre limpieza de historial.
- `cloud-sql-proxy.exe` (binario ~35MB) — sigue trackeado en git.
- `reset_db.py` — utilidad de dev legítima, se mantiene.
- `check_duplicates.py` — script del usuario en la raíz del repo (no es del backend), no se tocó.

## Migraciones (3 nuevas en esta tanda completa)

| Revisión | Depende de | Fase | Qué agrega |
|---|---|---|---|
| `d40754a1ed06` | `9261c6f3fe42` (head original) | C1 | Expand completo: nuevas tablas, columnas, saneamiento de huérfanos, índices únicos parciales |
| `c941d511ae50` | `d40754a1ed06` | D.1 | `usuario_planta.activo` + índice único parcial |
| `3dfb6eb20b53` | `c941d511ae50` | E2 | `lite_eventos_produccion.tiempo_ideal_seg` |

Head actual: `3dfb6eb20b53`. Estrategia: expand pura en las tres. `codigo_sku`/`id_orden` siguen como PK legacy hasta la fase contract (C2, no incluida).

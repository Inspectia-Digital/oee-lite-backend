# Production-grade multi-tenant and OEE refactor — Fases A, B1, C1

Base: `main` (`2579d39`) → rama `hardening/production-grade`.
Destino sugerido de este PR: `dev` (entorno con datos reales en Cloud SQL).

## Qué incluye

**Fase A — Seguridad e infraestructura**
- Elimina 3 rutas de emergencia (`/ruta-secreta`, `/ascender-estanislao`,
  `/setup/primer-admin`) y 4 scripts que hardcodeaban un `auth0_id`
  personal y ascendían usuarios a SUPERADMIN por SQL directo.
- `SQLModel.metadata.create_all()` pasa de incondicional a opt-in vía
  `AUTO_CREATE_TABLES` (default `false`).
- Settings centralizado (pydantic-settings) con validación estricta en
  producción: rechaza `CORS_ORIGINS` vacío/`*`, `DATABASE_ECHO=true`,
  `AUTO_CREATE_TABLES=true`, Auth0 sin configurar.
- `/health` se separa en `/health/live` (sin dependencias) y
  `/health/ready` (valida conexión real a Postgres).
- JWKS con timeout, errores de auth sanitizados.
- `requirements.txt` normalizado (estaba en UTF-16 con BOM).

**Fase B1 — Modelo de datos 2.0**
- `activo` en Planta, Línea, Operario, Supervisor, Turno, MotivoParada,
  MaestroSKU, OrdenProduccion.
- `Tenant.estado` (ACTIVO/UI_SUSPENDIDA/SUSPENSION_TOTAL) — el enum existe,
  la lógica de suspensión (Fase D) todavía no está implementada.
- `Planta.timezone` (IANA).
- Nuevas tablas: `Maquina`, `MaquinaEstacion`, `UsuarioPlanta`,
  `ApiKeyDispositivo` — los modelos existen, los endpoints/lógica de uso
  (Fase D) todavía no.
- `LiteEventoProduccion` (motor universal OEE Lite) gana `maquina_id`,
  `unidades_rechazadas`, `event_id` + `payload_hash` (idempotencia),
  `incluido_oee` (snapshot inmutable = estado de la estación al momento
  del evento).
- `MaestroSKU`/`OrdenProduccion` ganan un `id` UUID interno; `codigo_sku`/
  `id_orden` siguen siendo la PK legacy hasta la fase contract (C2).

**Fase C1 — Migración expand (Alembic `d40754a1ed06`)**
- Estrategia expand pura: agrega, no retira nada.
- Backfill de `id` UUID en SKU/Orden.
- Saneamiento de huérfanos: crea "Planta Default"/"Línea Default" por
  tenant (UUIDv5 determinístico) y reasigna líneas/estaciones sin padre
  **antes** de imponer `NOT NULL` en `planta_id`/`linea_id`.
- Índices únicos parciales por tenant sobre filas activas (permite
  reutilizar código tras una baja lógica).

**Fixes adicionales encontrados durante la verificación**
- `Linea`/`Estacion.modo_asignacion_operarios` mandaban el `.name` del
  enum en vez del `.value`, rompiendo cualquier insert real (bug
  preexistente, no introducido por este PR). Corregido.
- Fallback de transición para `AUTH0_DOMAIN`/`AUTH0_AUDIENCE` y
  `CORS_ORIGINS` fuera de producción, con warning en logs, para no
  romper login/CORS en `dev` si esas variables no están seteadas
  explícitamente en Cloud Run. En producción siguen siendo obligatorias.

## Qué NO incluye (a propósito)

- Fase D (M2M, suspensión de tenant, RBAC geolocalizado) — modelos
  listos, lógica pendiente.
- Fase E1/E2 (idempotencia real en Edge, fórmulas OEE corregidas).
- Fase C2 (contract: retirar PK legacy de SKU/Orden).
- Suite de tests automatizada (QA-01 sigue abierto).
- Rotación de `auth.txt` / eliminación de `cloud-sql-proxy.exe` del repo.

## Impacto en datos reales de `dev`

- **No hay pérdida de datos.** Todas las columnas nuevas son opcionales
  o tienen `server_default` explícito; los huérfanos se sanean antes de
  cualquier `NOT NULL`.
- Usuarios y roles existentes (incluidos superadmins) no se tocan.
  `tenants_saas.estado` se agrega con default `activo` para todos los
  tenants existentes — sin cambio de comportamiento (la lógica de
  suspensión todavía no existe).
- Ver `docs/ROLLBACK_PRODUCTION_GRADE.md` antes de mergear: incluye
  checklist de variables de entorno a confirmar en Cloud Run.

## Cómo probé esto (ver docs/COMANDOS_PRODUCTION_GRADE.md para el detalle)

1. Migración sobre base vacía: un solo paso, sin errores.
2. Migración sobre datos legacy con huérfanos reales insertados a mano
   (línea sin planta, estación sin línea, SKU/orden sin `id` UUID):
   saneamiento verificado por consulta directa a Postgres.
3. Segunda ejecución de `upgrade head`: no-op. Downgrade + re-upgrade:
   reversible, sin pérdida de las asignaciones ya hechas.
4. `seed.py` completo + smoke test de la app vía `TestClient`:
   `/health/live`, `/health/ready` (con DB real) responden 200; las 3
   rutas de emergencia responden 404.
5. `pip check` limpio, `compileall` sin errores.

**No ejecutado** (warning explícito): `pip-audit`, escaneo de secretos,
build de imagen Docker, tests automatizados (no existen todavía).

## Plan de fases restante

B1 → C1 (este PR) → D → E1 → E2 → API/CRUD completo → C2 → tests/docs.

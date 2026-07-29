# Production-grade multi-tenant and OEE refactor — Fases A a tests

Base: `main` (`2579d39`) → `main` actual (`643a3f6`). Todo commiteado directo
a `main` (flujo acordado: push a `main` lo hace el asistente, el PR real
`main → dev` lo abre el usuario manualmente en GitHub).

## Qué incluye, fase por fase

### Fase A — Seguridad e infraestructura
- Elimina 3 rutas de emergencia y 4 scripts que otorgaban SUPERADMIN por SQL directo sin RBAC.
- `SQLModel.metadata.create_all()` pasa a opt-in (`AUTO_CREATE_TABLES`).
- Settings centralizado con validación estricta en producción (CORS, echo, auto-create, Auth0).
- `/health/live` + `/health/ready` separados. JWKS con timeout, errores sanitizados.
- `requirements.txt` normalizado (estaba en UTF-16 con BOM pese a que la auditoría decía que ya se había corregido).

### Fase B1 — Modelo de datos 2.0
`activo` en los maestros, `Tenant.estado`, `Planta.timezone`, tablas nuevas
(`Maquina`, `MaquinaEstacion`, `UsuarioPlanta`, `ApiKeyDispositivo`),
columnas de idempotencia/snapshot en `LiteEventoProduccion`, identidad UUID
interna en SKU/Orden (PK legacy se mantiene hasta C2).

### Fase C1 — Migración expand
Sanea huérfanos (Planta/Línea Default con UUIDv5 determinístico) antes de
imponer `NOT NULL`; índices únicos parciales por tenant sobre filas activas.
Probada contra datos legacy con huérfanos insertados a mano, verificado por
consulta directa a Postgres.

### Fase D — M2M, suspensión, RBAC geolocalizado
- **D.1**: CRUD de `ApiKeyDispositivo` (`key_id.secret`, hash bcrypt, máx. 2
  activas por estación) y `UsuarioPlanta`.
- **D.2**: Suspensión real vía `Tenant.estado` — reemplaza un kill-switch que
  desactivaba usuarios uno por uno y nunca tocaba el campo (hallazgo USR-009).
  `UI_SUSPENDIDA` bloquea humano pero no Edge; `SUSPENSION_TOTAL` bloquea ambos.
- **D.3**: `validar_planta()` pasó de chequear sólo que existiera el header a
  validar la asignación real en `UsuarioPlanta`. Supervisor sin planta → 403;
  listado de usuarios acotado a quienes comparten planta. Se deshabilitó
  (410) el endpoint de asignación retroactiva de operarios: nunca persistía
  nada (intentaba escribir un campo que no existía en el modelo — STP-007).
- **D.4a**: autenticación M2M real (`X-Device-Key: key_id.secret`) para el
  único endpoint Edge realmente registrado (`scans.py`).
- **D.4b**: login de operario por escaneo de legajo, escribe/actualiza
  `AsignacionTurno` sin tocar eventos históricos.

**Decisión de diseño relevante**: se detectó que `app/api/plc_router.py`
(pensado para Green Mills/Node-RED) nunca estuvo registrado en `main.py`.
Se decidió con el usuario mantener **un solo pipeline de ingesta**
(`scans.py`) clasificando por tipo de dispositivo más adelante, en vez de
tener un endpoint separado por cliente.

### Fase E1 — Edge, timezone e idempotencia
`event_id` obligatorio con idempotencia real (200 si se repite igual, 409 si
cambia el payload), timestamps naive interpretados con timezone de planta y
convertidos a UTC, rechazo de timestamps fuera de rango, `maquina_id`
inconsistente se acepta con warning, `incluido_oee` como snapshot real.

### Fase E2 — Motor OEE (fórmulas corregidas)
Tres bugs de negocio reales corregidos:
- Rendimiento no multiplicaba por `unidades_procesadas` (subestimaba lotes).
- El tiempo perdido en paradas usaba el delta completo en vez del excedente
  sobre tolerancia (doble penalización de Disponibilidad y Rendimiento).
- Calidad devolvía 100% cuando no había datos, en vez de N/A.

Alcance acotado deliberadamente a `oee-general`; el resto de `analytics.py`
(dashboard, pareto, cuellos de botella, alertas vivas) queda documentado
como pendiente (sigue sin filtrar `incluido_oee`, sigue truncando a 1000
eventos antes de agregar, sigue agrupando por nombre en vez de ID).

### API/CRUD completo
- Bug real en vivo corregido: `jerarquia.py` (registrado y en uso) usaba
  `id: int` para Planta/Línea/Estación, que son UUID desde siempre —
  cualquier llamada real fallaba.
- `POST /accesos/mi-empresa/sub-tenants` no tenía ningún RBAC — cualquier
  usuario autenticado podía crear una planta.
- CRUD completo (detalle + PATCH + baja lógica + `incluir_inactivos`) para
  Planta, Línea, Estación, Motivo de Parada, Turno, SKU.
- CRUD de Operario y Supervisor construido desde cero (no existía).
- CRUD completo de Orden de Producción construido desde cero (el único
  código que las tocaba, `erp.py`, no está registrado en `main.py`).
- Dos hard-deletes reales corregidos a baja lógica: `DELETE /config/estaciones/{id}`
  y `DELETE /superadmin/usuarios/{auth0_id}`.
- Detalle/PATCH de Tenants globales, detalle de usuarios (global y por
  tenant, respetando el alcance geolocalizado de D.3).

### Tests
44 tests de pytest contra Postgres real (nunca mocks para RBAC/triggers),
cubriendo: rutas de emergencia ausentes, config productiva, aislamiento
multi-tenant (incluyendo 404 en vez de 403 para no confirmar existencia),
suspensión de tenant, RBAC geolocalizado, M2M (máx. 2 keys, revocación,
suspensión total), idempotencia Edge, timestamps fuera de rango, máquina
inconsistente, fórmulas OEE contra datos conocidos, calidad N/A, baja
lógica y reactivación con reutilización de código.

## Qué NO incluye (a propósito)

- **Fase C2 (contract)**: retirar PK legacy de SKU/Orden. El HANDOFF pide
  explícitamente validar todo en un entorno real antes de esta fase — es la
  más riesgosa (retira columnas).
- Higiene general del resto de `analytics.py` (ver Fase E2 arriba).
- CI para correr la suite de tests automáticamente: crearía datos de prueba
  contra la base compartida de `dev`; necesita una base de test efímera
  separada, es trabajo de infraestructura aparte.
- Rotación de `auth.txt` / eliminación de `cloud-sql-proxy.exe` del repo.
- `pip-audit` / escaneo de secretos (no disponibles en este entorno).

## Impacto en datos reales de `dev`

- **No hay pérdida de datos** en ninguna de las 3 migraciones (todas son
  expand puro, con `server_default` explícito y saneamiento antes de
  cualquier `NOT NULL`).
- El pipeline de CI (`deploy-dev.yml`) ya corre las migraciones
  automáticamente contra Cloud SQL antes de cada deploy — confirmado
  funcionando en un `workflow_dispatch` real.
- **Cambio de comportamiento real a tener en cuenta**: `UsuarioPlanta` es
  una tabla nueva, sin ninguna asignación real todavía. Los supervisores/
  operarios que ya usan las pantallas operativas van a empezar a recibir
  403 hasta que se los asigne a su planta vía
  `POST /accesos/mi-empresa/usuario-planta` (ver
  `docs/CAMBIOS_FRONTEND_REQUERIDOS.md`).
- Un endpoint cambió de contrato: `PATCH /superadmin/tenants/{id}/estado`
  pasó de `?activo=true/false` a body `{"estado": "..."}`.

## Cambios de contrato de API para el front

Ver `docs/CAMBIOS_FRONTEND_REQUERIDOS.md` — documento dedicado con todos
los endpoints nuevos, los que cambiaron de forma, y los pasos operativos
necesarios (asignar plantas a supervisores existentes) antes de poder
probar todo junto.

## Plan de fases restante

~~B1~~ → ~~C1~~ → ~~D~~ → ~~E1~~ → ~~E2~~ → ~~API/CRUD completo~~ → ~~tests~~ → **C2** → docs finales de cierre.

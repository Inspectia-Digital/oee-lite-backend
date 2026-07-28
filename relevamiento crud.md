# Relevamiento de endpoints CRUD y RBAC

**Estado del documento:** análisis y propuesta; **no implementa endpoints ni modifica comportamiento de la API**.  
**Fecha:** 28 de julio de 2026.  
**Objetivo:** registrar qué operaciones existen, cuáles faltan y qué permisos deberían aplicarse antes de preparar archivos de reemplazo y PR separados.

Este relevamiento complementa la [auditoría de producción](AUDITORIA_PRODUCCION.md) y el [plan de implementación local](PLAN_IMPLEMENTACION_LOCAL.md). Ninguna fila “propuesta” debe considerarse aprobada hasta validar reglas de negocio, estrategia de borrado y contratos con el frontend.

## Convenciones

- **C**: crear (`POST`).
- **R-L**: listar (`GET` de colección).
- **R-D**: obtener detalle (`GET /{id}`).
- **U**: actualizar (`PATCH`).
- **D**: eliminar o desactivar (`DELETE`).
- ✅ Disponible actualmente.
- 🟡 Parcial o con controles insuficientes.
- ❌ No disponible.
- **Maestro**: configuración editable de la organización.
- **Transaccional**: evidencia operativa; no debería exponerse como CRUD genérico.

## Resultado ejecutivo

Los CRUD administrativos no están completos ni aplican una política RBAC uniforme. Estaciones es el maestro más cercano a un CRUD completo, aunque carece de endpoint de detalle. Líneas, plantas, motivos y turnos sólo tienen algunas operaciones. Operarios y supervisores no tienen CRUD administrativo. Usuarios cuenta con operaciones parciales, pero el `PATCH` del tenant no exige inicialmente un rol de administración, lo que debe tratarse como hallazgo de seguridad antes de exponer más funcionalidad.

## Política RBAC propuesta para aprobación

| Capacidad | `OPERARIO` | `PRODUCCION` | `SUPERVISOR` | `GERENCIA` | `SUPERADMIN` |
|---|---:|---:|---:|---:|---:|
| Leer maestros del propio tenant | ✅ | ✅ | ✅ | ✅ | ✅ |
| Crear/editar maestros del propio tenant | ❌ | ❌ | ❌ | ✅ | ✅ |
| Eliminar/desactivar maestros | ❌ | ❌ | ❌ | ✅ | ✅ |
| Listar/ver usuarios del tenant | ❌ | ❌ | ✅ | ✅ | ✅ |
| Crear/editar/desactivar usuarios del tenant | ❌ | ❌ | ❌ | ✅ | ✅ |
| Asignar rol `SUPERADMIN` | ❌ | ❌ | ❌ | ❌ | ✅ |
| Administrar tenants y usuarios globales | ❌ | ❌ | ❌ | ❌ | ✅ |
| Impersonar tenant | ❌ | ❌ | ❌ | ❌ | ✅ |

Decisiones pendientes:

1. Confirmar si `SUPERVISOR` puede leer operarios/supervisores de toda la empresa o sólo de su planta.
2. Confirmar si `PRODUCCION` necesita lectura de SKUs, órdenes y turnos.
3. Definir si las bajas de maestros son físicas o lógicas. Para producción se recomienda baja lógica cuando exista historial.
4. Definir permisos específicos para identidad M2M de PLC/kioscos; no deben heredar roles humanos.

## Inventario actual y faltantes

### Organización y configuración

| Recurso | C | R-L | R-D | U | D | Observación actual |
|---|---:|---:|---:|---:|---:|---|
| Planta | 🟡 | ✅ | ❌ | ❌ | ❌ | Crear planta está autenticado por contexto, pero no exige Gerencia/SuperAdmin. |
| Línea | ✅ | ✅ | ❌ | ❌ | ❌ | Creación valida que la planta pertenezca al tenant y exige Gerencia/SuperAdmin. |
| Estación | ✅ | ✅ | ❌ | ✅ | ✅ | Mutaciones exigen Gerencia/SuperAdmin; faltan detalle y validaciones completas al mover relaciones. |
| Motivo de parada | ✅ | ✅ | ❌ | ❌ | ❌ | Creación protegida; no existe mantenimiento posterior. |
| Turno | 🟡 | ✅ | ❌ | ❌ | ❌ | Creación protegida, pero debe verificarse que `linea_id` pertenezca al tenant. |
| Operario | ❌ | ❌ | ❌ | ❌ | ❌ | El modelo existe; no hay router CRUD administrativo. |
| Supervisor | ❌ | ❌ | ❌ | ❌ | ❌ | El modelo existe; no hay router CRUD administrativo. |
| Asignación de turno | ❌ | ❌ | ❌ | ❌ | ❌ | Antes de implementarlo se requieren reglas de solapamiento, vigencia y planta. |

### Catálogo y planificación

| Recurso | C | R-L | R-D | U | D | Observación actual |
|---|---:|---:|---:|---:|---:|---|
| SKU | 🟡 | ✅ | ❌ | 🟡 | ❌ | Hay carga masiva que crea/actualiza y listado. `codigo_sku` es PK global: riesgo de colisión entre tenants. |
| Orden de producción | 🟡 | ❌ | ❌ | 🟡 | ❌ | Hay upsert ERP. `id_orden` es PK global y debe corregirse antes de CRUD manual. |

### Usuarios y SaaS

| Recurso | C | R-L | R-D | U | D | Observación actual |
|---|---:|---:|---:|---:|---:|---|
| Perfil propio | N/A | N/A | ✅ | ❌ | ❌ | Existe `GET /accesos/usuarios/me`; falta decidir edición de nombre/perfil. |
| Usuarios del tenant | ✅ | ✅ | ❌ | 🟡 | ❌ | Crear/listar tienen checks de rol; actualizar carece de un gate inicial Gerencia/SuperAdmin y sólo aplica restricciones parciales. |
| Configuración del tenant propio | N/A | N/A | ✅ | ✅ | N/A | La edición está limitada a Gerencia/SuperAdmin. No se propone borrar el tenant desde este contexto. |
| Tenants globales | ✅ | ✅ | ❌ | 🟡 | ❌ | Sólo SuperAdmin; la actualización actual se limita al endpoint de estado de usuarios. |
| Usuarios globales | ✅ | ✅ | ❌ | ✅ | ✅ | Sólo SuperAdmin; falta detalle individual. El delete físico requiere revisión de auditoría. |

### Registros transaccionales

No se propone CRUD genérico para scans, eventos de producción, ciclos PLC, paradas detectadas ni métricas. Son evidencia operativa y deberían usar comandos de dominio, por ejemplo `clasificar`, `anular`, `corregir` o `reprocesar`, dejando usuario, fecha, valor anterior, valor nuevo y motivo. Un `DELETE` genérico dañaría trazabilidad y cálculos OEE.

## Endpoints propuestos, todavía no aprobados

### Plantas

- `GET /accesos/mi-empresa/sub-tenants/{planta_id}`
- `PATCH /accesos/mi-empresa/sub-tenants/{planta_id}`
- `DELETE /accesos/mi-empresa/sub-tenants/{planta_id}` — preferentemente baja lógica.
- Agregar RBAC Gerencia/SuperAdmin al `POST` existente.

### Líneas

- `GET /config/lineas/{linea_id}`
- `PATCH /config/lineas/{linea_id}`
- `DELETE /config/lineas/{linea_id}` — definir bloqueo o baja en cascada cuando tenga estaciones.

### Estaciones

- `GET /config/estaciones/{estacion_id}`
- Ampliar `PATCH` para relaciones sólo si se aprueba mover estación de línea/parent; validar tenant y ciclos.

### Motivos de parada

- `GET /config/motivos-parada/{motivo_id}`
- `PATCH /config/motivos-parada/{motivo_id}`
- `DELETE /config/motivos-parada/{motivo_id}` — baja lógica si ya fue usado.

### Turnos

- `GET /config/turnos/{turno_id}`
- `PATCH /config/turnos/{turno_id}`
- `DELETE /config/turnos/{turno_id}` — baja lógica si tiene asignaciones.
- Validar tenant de `linea_id` en create/update.

### Operarios y supervisores

- Colección y detalle para `GET`, `POST`, `PATCH` y `DELETE` bajo `/config/operarios` y `/config/supervisores`.
- Unicidad de `legajo` por tenant, no necesariamente global.
- Definir baja lógica para conservar producción histórica.

### Usuarios

- `GET /accesos/mi-empresa/usuarios/{auth0_id}`
- `DELETE /accesos/mi-empresa/usuarios/{auth0_id}` como desactivación, no borrado físico.
- Corregir primero el `PATCH` existente para exigir Gerencia/SuperAdmin, impedir autodegradación/autodesactivación y bloquear a Gerencia frente a SuperAdmin.
- `GET /accesos/superadmin/usuarios/{auth0_id}` para detalle global.

### Tenants globales

- `GET /accesos/superadmin/tenants/{tenant_id}`
- `PATCH /accesos/superadmin/tenants/{tenant_id}`
- `DELETE /accesos/superadmin/tenants/{tenant_id}` como suspensión coordinada de tenant y usuarios; no borrado físico.

## Controles transversales obligatorios para cualquier implementación

1. Toda query de detalle, update o delete debe filtrar simultáneamente por ID y `tenant_id`.
2. Toda referencia recibida (`planta_id`, `linea_id`, `parent_id`, etc.) debe comprobar pertenencia al tenant antes de persistir.
3. La API debe responder `404` ante un UUID de otro tenant para no confirmar su existencia.
4. Mutaciones deben tener dependencia RBAC explícita y pruebas de `403` para cada rol no autorizado.
5. Conflictos de integridad deben hacer rollback y devolver `409`, no dejar la sesión inutilizable.
6. No aceptar payloads vacíos ni `null` en columnas obligatorias mediante `PATCH`.
7. Incorporar paginación, orden y filtros en colecciones antes de crecer volumen.
8. Auditar altas, cambios de rol, bajas y operaciones SuperAdmin.
9. Evitar cascadas físicas implícitas sobre producción histórica.
10. Probar acceso cruzado con al menos dos tenants y dos plantas por tenant.

## División sugerida en PR manuales

Para facilitar revisión y reemplazo de archivos locales, no se recomienda un único PR masivo:

1. **PR-RBAC:** helper RBAC común y corrección de permisos de usuarios/plantas, sin agregar CRUD.
2. **PR-ORG:** CRUD de plantas, líneas y estaciones.
3. **PR-PERSONAS:** CRUD de operarios, supervisores, turnos y asignaciones.
4. **PR-MOTIVOS:** mantenimiento de motivos con estrategia de baja aprobada.
5. **PR-SAAS:** detalle y baja lógica de usuarios/tenants globales.
6. **PR-IDENTIDAD:** migración de claves de SKU/orden para aislamiento real por tenant.
7. **PR-CATALOGO:** CRUD de SKU y órdenes después de la migración.

Cada PR debe incluir migración si corresponde, tests RBAC/tenant, comandos de verificación, lista exacta de archivos y rollback. Sólo después de aprobación se deben generar los archivos completos para reemplazo local.

## Formato de entrega posterior a la aprobación

Cuando se apruebe un PR, la entrega manual debería contener:

- Carpeta separada por PR, sin mezclar etapas.
- Archivos completos nuevos/modificados, conservando su ruta relativa (`app/...`, `alembic/...`, `tests/...`).
- `MANIFEST.md` con SHA base, archivos a reemplazar/agregar/eliminar y orden de aplicación.
- `COMANDOS.md` con instalación, migración, tests, ejecución y rollback.
- Patch `.diff` opcional para inspección, además de los archivos completos solicitados.
- Variables nuevas sólo como `.env.example`, nunca valores reales.
- Resultado esperado de cada prueba y criterio de aceptación.

Así se podrán aplicar los PR manualmente y en orden sobre el repositorio local, revisar el diff con `git diff`, ejecutar pruebas y promover únicamente los cambios aprobados.

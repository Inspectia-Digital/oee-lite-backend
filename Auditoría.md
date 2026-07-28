# Auditoría técnica previa a producción

**Fecha:** 28 de julio de 2026  
**Alcance:** código, autenticación, aislamiento multi-tenant, configuración, dependencias, contenedores, base de datos, migraciones y operación.  
**Conclusión:** **NO GO para producción** hasta cerrar los hallazgos críticos y altos y obtener evidencia de pruebas en un entorno equivalente.

## Resumen ejecutivo

La aplicación tiene una base funcional, autenticación Auth0 y separación lógica por tenant, pero todavía no cuenta con las garantías necesarias para producción. Los mayores riesgos son la exposición histórica de secretos, ausencia de pruebas automatizadas, migraciones no verificadas sobre datos reales, posibles carreras/duplicados en la ingesta y falta de controles específicos para dispositivos PLC/kiosco.

Durante la primera etapa de endurecimiento se prepararon correcciones para eliminar secretos del árbol actual, retirar rutas de elevación de privilegios, hacer explícita la configuración sensible, restringir CORS, evitar `create_all()` en producción y reforzar consultas de jerarquía. Estas correcciones **no deben promoverse a ciegas**: primero deben revisarse y probarse siguiendo el plan local.

## Escala de severidad

| Nivel | Criterio | Tratamiento |
|---|---|---|
| Crítico | Permite compromiso de cuentas/datos o administración no autorizada | Bloquea cualquier despliegue |
| Alto | Puede causar fuga entre tenants, pérdida/duplicación de datos o despliegue irrecuperable | Resolver antes de staging/prod |
| Medio | Afecta disponibilidad, diagnóstico o mantenimiento | Resolver o aceptar formalmente con mitigación |
| Bajo | Deuda técnica sin impacto inmediato | Planificar y monitorear |

## Hallazgos

### SEC-01 — secretos versionados (Crítico)

- Se encontró una credencial en `auth.txt` y un secreto Auth0 M2M como valor por defecto en código.
- Se eliminaron del árbol actual, pero permanecen recuperables desde el historial Git.
- **Acción obligatoria externa al código:** revocar/rotar ambos valores, revisar logs de Auth0/Google y documentar fecha, responsable y evidencia. Luego evaluar limpieza de historial coordinada; reescribirlo sin coordinación puede romper clones y referencias.
- **Criterio de cierre:** secretos anteriores revocados, nuevos secretos en Secret Manager y escaneo del repositorio/historial sin credenciales vigentes.

### SEC-02 — endpoints de bootstrap administrativo (Crítico)

- Existían rutas capaces de crear o promover un usuario conocido a `SUPERADMIN`.
- Se retiraron del arranque. Debe confirmarse que ningún script, versión antigua o servicio desplegado siga exponiéndolas.
- **Criterio de cierre:** smoke test devuelve `404` para las tres rutas retiradas y búsqueda en todas las ramas/artefactos sin coincidencias.

### TEN-01 — aislamiento multi-tenant inconsistente (Alto)

- Algunas consultas de jerarquía filtraban la planta inicialmente, pero no repetían `tenant_id` al cargar líneas/estaciones.
- Se preparó el filtrado defensivo, pero falta una suite que cree dos tenants con identificadores cruzados y demuestre que nunca se devuelven datos ajenos.
- Debe auditarse del mismo modo cada query, join, update y delete; el filtro en la capa API no reemplaza controles en repositorio/BD.
- **Criterio de cierre:** tests positivos y negativos para todos los endpoints tenant-aware.

### AUTH-01 — identidad inadecuada para PLC/kioscos (Alto)

- La ingesta reutiliza el flujo de usuarios Auth0; no hay identidad, scopes ni rotación específicos por dispositivo.
- Definir clientes M2M, audiencia/scopes mínimos, asociación tenant/planta/estación, revocación, rate limit y auditoría.
- **Criterio de cierre:** un token de dispositivo sólo puede escribir en sus estaciones y no acceder a endpoints humanos/administrativos.

### DATA-01 — concurrencia e idempotencia de scans (Alto)

- Leer “último evento”, calcular delta e insertar no constituye una operación serializada.
- Requests simultáneos o reintentos pueden duplicar producción, crear paradas falsas o calcular deltas incorrectos.
- Incorporar `event_id`/clave idempotente del emisor, constraint único, estrategia transaccional y prueba concurrente.
- **Criterio de cierre:** reintentar el mismo evento no altera totales; concurrencia preserva orden/reglas definidas.

### DB-01 — migraciones frágiles (Alto)

- La revisión `9261c6f3fe42` crea ENUMs sin tolerancia a estados parciales y hace downgrade de constraints sin nombres explícitos.
- `create_all()` no sustituye migraciones y puede producir drift; quedó opt-in y debe permanecer deshabilitado fuera del desarrollo descartable.
- **Criterio de cierre:** upgrade desde snapshot anonimizado, verificación de schema, smoke test, rollback ensayado y backup restaurable.

### QA-01 — ausencia de suite automatizada (Alto)

- No existe una barrera de regresión para auth, RBAC, tenants, imports, analytics o ingesta.
- Mínimo requerido: unitarios de reglas, integración con PostgreSQL, tests de API y smoke tests del contenedor.
- **Criterio de cierre:** suite reproducible en local y CI; fallos bloquean promoción.

### DEP-01 — build no reproducible (Alto)

- `requirements.txt` estaba en UTF-16 y contenía una combinación que falló con el entorno auditado (`sqlmodel==0.0.16`/Pydantic instalado).
- Se normalizó a UTF-8 y se propuso un pin compatible, pero aún debe verificarse en un virtualenv Python 3.11 vacío. Un `pip check` sobre el entorno antiguo no prueba el lock nuevo.
- **Criterio de cierre:** instalación limpia, importación de la app, suite completa, imagen construida y dependencias con hashes/escaneo.

### OPS-01 — health sin readiness (Medio)

- `/health` sólo prueba que el proceso atiende HTTP; no confirma PostgreSQL ni revisión Alembic.
- Separar liveness (sin dependencias) y readiness (consulta liviana + schema esperado), con timeouts estrictos.

### OPS-02 — observabilidad y límites incompletos (Medio)

- Faltan logs JSON/correlation ID, métricas/alertas, política de PII, rate limiting y límites de uploads.
- Varias capturas genéricas de excepciones dificultan diagnóstico y pueden ocultar fallos parciales.

### PERF-01 — I/O bloqueante y dimensionamiento no medido (Medio)

- Endpoints síncronos, pandas y llamadas `requests` pueden ocupar workers; no hay evidencia de prueba de carga ni dimensionamiento del pool SQL.
- Medir p50/p95/p99, errores y saturación con carga representativa antes de ajustar Cloud Run/workers/pool.

## Correcciones preparadas para revisión local

- Configuración obligatoria y centralizada para DB/Auth0/CORS; wildcard CORS rechazado en producción.
- Timeouts y errores públicos controlados en Auth0.
- `DATABASE_ECHO=false`, `pool_pre_ping` y `AUTO_CREATE_TABLES=false` por defecto.
- Rutas administrativas temporales retiradas.
- UUID y filtros tenant defensivos en jerarquía.
- Secretos/Cloud SQL Proxy fuera del árbol e imagen; `.dockerignore` añadido.
- PostgreSQL local expuesto sólo en loopback y con healthcheck.
- Requisitos convertidos a UTF-8 y propuesta de compatibilidad Pydantic.

## Decisión de salida

| Entorno | Estado recomendado | Condición |
|---|---|---|
| Local | Autorizado | Usar datos sintéticos y seguir el plan incremental |
| `main` | No promover aún | Revisión de diff + suite local limpia |
| `dev` | No promover aún | CI verde, migración y smoke tests integrados |
| `prod` | Bloqueado | Críticos/altos cerrados, backup/rollback probado y aprobación explícita |

Cada hallazgo debe convertirse en un ticket con responsable, fecha objetivo, evidencia y decisión. “Código modificado” no significa “riesgo cerrado”.

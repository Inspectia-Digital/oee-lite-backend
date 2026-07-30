# Cambios de backend — Fases F a J (cierre de gaps InspectIA OS)

Este documento complementa a `docs/CAMBIOS_FRONTEND_REQUERIDOS.md` (que
cubre Fases A–E2 + tests). Acá está todo lo nuevo que resuelve los ítems
que ustedes mismos habían detectado como faltantes (el reporte de 5 áreas)
y que `BACKEND_REQUIREMENTS.md §14` marcaba como "Endpoints pendientes
(auditoría OpenAPI v2.0)". Todo está documentado interactivamente en
`https://api-dev.tymeo.inspectia.ai/docs` (Swagger UI) con un token Bearer
de Gerencia/SuperAdmin.

**Importante para arrancar:** este backend (`oee-lite-backend`, el módulo
Tymeo) **no** implementa la jerarquía de tenants de `BACKEND_REQUIREMENTS.md
§2.1` (tenants como filas con `tipo='empresa'/'planta'` y `parent_id`), ni
existía hasta ahora ninguna matriz de permisos por módulo. Eso se resolvió
en la Fase F (ver abajo) pero **adaptado al modelo real** de este backend:
`Planta` sigue siendo una tabla propia (no un `Tenant` con `tipo='planta'`),
y el "sub-tenant" que ya usan (`X-Sub-Tenant-Id`) sigue resolviendo a un
`Planta.id`, no a un `Tenant.id`. Si en algún lugar del front hay código que
asume que un sub-tenant es un `Tenant` (con `logo_url`, `tipo`, `parent_id`
propios), no va a encontrar esos campos — la Planta sólo tiene `id`,
`nombre`, `ubicacion`, `timezone`, `activo`.

Factory OEE Hub (`/hub/*`) sigue **fuera de alcance** de este backend —
es un producto con modelo de datos y dominio distintos (carga batch/manual
vs. sensores en tiempo real), documentado en `BACKEND_REQUIREMENTS.md §5`.
No hay nada nuevo en esa área.

---

## Fase F — Permisos por módulo y planta

### `GET /accesos/usuarios/me` — campo nuevo `permisos[]`

Aditivo, no rompe nada: el resto de los campos son los mismos de siempre.

```json
{
  "id": "...", "auth0_id": "...", "tenant_id": "...", "email": "...",
  "rol": "supervisor", "activo": true, "nombre": "...", "apellido": "...",
  "permisos": [
    { "modulo": "tymeo", "planta_id": "uuid-de-planta-o-null", "rol": "supervisor" }
  ]
}
```

- **Gerencia/SuperAdmin/Producción**: reciben un permiso por cada módulo en
  `Tenant.modulos_contratados`, con `planta_id: null` (alcance de toda la
  empresa) — no hace falta asignarles nada a mano.
- **Supervisor/Operario**: sólo aparecen los módulos/plantas que tengan
  explícitamente asignados (ver CRUD abajo). Si no tienen ninguno,
  `permisos: []`.
- Hoy el único módulo real es `"tymeo"`. Las claves `oee-hub`, `vision`,
  `logistica`, `seguridad` son válidas como string pero no tienen backend
  detrás todavía.

### CRUD de asignación (sólo Gerencia/SuperAdmin)

```
POST   /accesos/mi-empresa/modulo-permiso
       { "usuario_id": "...", "modulo": "tymeo", "planta_id": "...", "rol": "supervisor" }
       -> 201 { id, usuario_id, modulo, planta_id, rol, activo }
       -> 400 si el usuario_id es Gerencia/SuperAdmin/Producción (no lo necesitan)
       -> 409 si ya existe esa combinación (usuario, módulo, planta) activa

GET    /accesos/mi-empresa/modulo-permiso?usuario_id=&planta_id=&modulo=
DELETE /accesos/mi-empresa/modulo-permiso/{id}   (baja lógica, activo=false)
```

---

## Fase G — CRUD de Máquina

No existía ningún endpoint pese a que la ingesta (`POST /api/lite/scans`)
ya acepta y valida `maquina_id` desde la Fase E1.

```
POST   /config/maquinas/                     { "codigo_externo": "PLC-01", "nombre": "..." }
GET    /config/maquinas/?incluir_inactivos=
GET    /config/maquinas/{id}
PATCH  /config/maquinas/{id}                 { "nombre"?, "codigo_externo"?, "activo"? }
DELETE /config/maquinas/{id}                 (baja lógica)
```

`codigo_externo` único por tenant entre máquinas activas (409 si se
duplica; reutilizable tras baja lógica, mismo patrón que legajos).

Asociación N:N con Estación (para que la ingesta valide `maquina_id`):

```
POST   /config/maquinas/{maquina_id}/estaciones   { "estacion_id": "..." }  -> 409 si ya está asociada
GET    /config/maquinas/{maquina_id}/estaciones
DELETE /config/maquinas/{maquina_id}/estaciones/{asociacion_id}
```

---

## Fase H — Supervisión y control operativo

Resuelve las feature keys `supervisor.asignaciones` y `eventos.live` de
`BACKEND_REQUIREMENTS.md §14`, y el tablero de `§11.2`
(`SupervisionTurnosMatrix` / `useAsignacionSupervisores.ts`).

### Tablero de dotación (operario ↔ estación)

```
GET    /supervisor/asignaciones/?fecha=YYYY-MM-DD&linea_id=&turno_id=
POST   /supervisor/asignaciones/    { "fecha", "estacion_fk", "operario_fk", "turno_fk" }
DELETE /supervisor/asignaciones/{id}
```

- `POST` es **idempotente** por `(fecha, turno_fk, estacion_fk)`: reasignar
  sobrescribe al operario anterior, no duplica.
- `DELETE` **libera la estación** (hard-delete real de esa fila puntual —
  a diferencia de los maestros de configuración, esto es una agenda diaria
  sin necesidad de historial/reactivación).
- Requiere `X-Sub-Tenant-Id` (planta activa). Supervisor/Operario deben
  tener `UsuarioPlanta` para esa planta (mismo 403 de siempre si no la
  tienen). Gerencia/SuperAdmin no necesitan asignación.
- "Copiar día anterior" sigue siendo responsabilidad del front: `GET` con
  `fecha - 1` y después un lote de `POST` con la `fecha` nueva.

### Monitor de eventos en vivo

```
GET /supervisor/eventos/live?limite=100
```

Devuelve los últimos eventos de escaneo de la planta activa, más recientes
primero:

```json
[{ "estacion_id": "...", "operario_id": "uuid-o-null", "timestamp": "...", "estado": "OPTIMO" }]
```

**Importante:** `operario_id` es **best-effort**. El evento de escaneo no
guarda a qué turno pertenece (`LiteEventoProduccion` nunca tuvo ese campo),
así que se resuelve buscando la `AsignacionTurno` de esa estación para la
fecha del evento, sin matchear el turno exacto. Alcanza para un monitor en
vivo ("quién está probablemente en esta estación ahora"), **no** lo usen
como fuente de verdad para reportes de asistencia o nómina.

### Asignación de supervisores por día

Path a nivel raíz (no bajo `/supervisor`), tal como lo pedía
`BACKEND_REQUIREMENTS.md §11.2`:

```
GET  /asignaciones/supervisor/?fecha=YYYY-MM-DD&linea_id=
POST /asignaciones/supervisor/   { "fecha", "linea_id", "turno_id", "supervisor_id" }
```

También idempotente por `(fecha, linea_id, turno_id)`: reasignar
sobrescribe. Mismo requisito de `X-Sub-Tenant-Id` + RBAC geolocalizado que
el resto de `/supervisor/*`.

---

## Fase I — Analítica faltante

Resuelve `analytics.oee-cascada`, `analytics.rendimiento-secuencial`,
`analytics.reporte-produccion` y `command-center.summary` de
`BACKEND_REQUIREMENTS.md §14`. Las tres primeras reusan **exactamente** el
mismo motor de cálculo que `/analytics/oee-general` (mismo código, no hay
fórmulas duplicadas ni que puedan divergir).

### `GET /analytics/oee-cascada/?fecha_desde=&fecha_hasta=&linea_id=&turno_id=`

5 etapas en minutos, cada una ≤ la anterior:

```json
{
  "tiempo_calendario_min": 1440.0,
  "tiempo_planificado_min": 480.0,
  "tiempo_operativo_min": 460.0,
  "tiempo_neto_min": 410.0,
  "tiempo_efectivo_min": 350.2
}
```

Mapeo con el resto del dashboard: `neto/planificado` ≈ Disponibilidad,
`efectivo/neto` ≈ Rendimiento × Calidad.

### `GET /analytics/rendimiento-secuencial/?fecha=&linea_id=`

```json
[{ "estacion": "Armado", "posicion_linea": 1, "tiempo_ciclo_prom": 42.3, "objetivo": 40 }]
```

Ordenado por `posicion_linea` (secuencia física de la línea), para el
gráfico de rendimiento por posición.

### `GET /analytics/reporte-produccion/?fecha_desde=&fecha_hasta=`

Filas planas por estación y día, listas para exportar a Excel. Ambos
parámetros son obligatorios; `400` si `fecha_hasta < fecha_desde`.

```json
[{ "fecha": "2026-07-29", "estacion": "Armado", "total_piezas": 500, "optimos": 420, "lentos": 60, "alertas": 20, "rechazadas": 5, "tiempo_promedio_seg": 41.8 }]
```

### `GET /command-center/summary`

**No** lleva `X-Sub-Tenant-Id` — es multi-planta por diseño, para el home
del shell:

```json
{
  "oee_global": 78.4,
  "alertas_activas": 3,
  "plantas": [{ "id": "...", "nombre": "Planta Garín", "oee": 81.2, "estado": "con_datos" }],
  "infraestructura": { "estaciones_activas": 12, "estaciones_total": 14 }
}
```

- Gerencia/SuperAdmin/Producción ven todas las plantas del tenant.
  Supervisor/Operario sólo las que tengan asignadas por `UsuarioPlanta`
  (si no tienen ninguna, `plantas: []` y `oee_global: null`).
- `oee_global` es el promedio simple del OEE de hoy entre las plantas con
  datos (`null` si ninguna planta tiene eventos hoy).
- `estado` por planta: `"con_datos"` o `"sin_datos"` (no hay eventos hoy).
- **`infraestructura` es una interpretación nuestra**, no un contrato
  cerrado: `BACKEND_REQUIREMENTS.md §3` la deja como `{...}` sin especificar
  del todo. La definimos como estaciones activas/total del conjunto de
  plantas visibles (no hay telemetría real de conectividad de hardware más
  allá de las credenciales M2M emitidas). Avisen si necesitan otra forma.

---

## Fase J — Branding y reset de password

### `POST /accesos/mi-empresa/tenant/logo`

**No es upload de archivo** — no hay object storage conectado todavía (se
decidió con el equipo no inventar un bucket sin confirmarlo). El contrato
es JSON con la URL ya alojada donde sea que el front suba el archivo:

```
POST /accesos/mi-empresa/tenant/logo
{ "logo_url": "https://cdn.ejemplo.com/logos/acme.png" }
-> 200 { "logo_url": "https://cdn.ejemplo.com/logos/acme.png" }
```

Sólo Gerencia/SuperAdmin. Si en el futuro quieren upload real de archivo,
avisen y lo armamos (falta decidir bucket/proveedor).

### `POST /accesos/superadmin/usuarios/{auth0_id}/reset-password`

```
-> 200 { "ticket_url": "https://dev-....auth0.com/tickets/..." }
```

Genera un link de cambio de password de un solo uso vía Auth0 Management
API (no fuerza el reset directo). Sólo SuperAdmin.

**Estado actual: devuelve `503`.** Falta crear del lado de Auth0 una app
M2M separada autorizada contra la Management API (scope
`create:user_tickets`) y configurar `AUTH0_MGMT_CLIENT_ID` /
`AUTH0_MGMT_CLIENT_SECRET` como secretos del backend. El contrato de
respuesta ya está cerrado, así que se puede integrar la UI ya mismo contra
Swagger/staging; simplemente va a devolver `503` hasta que esa credencial
exista.

---

## Resumen: qué feature key de `BACKEND_REQUIREMENTS.md §14` queda resuelta

| Feature key | Estado |
|---|---|
| `supervisor.asignaciones` | ✅ Resuelto (Fase H) |
| `eventos.live` | ✅ Resuelto (Fase H) — `operario_id` best-effort |
| `command-center.summary` | ✅ Resuelto (Fase I) — `infraestructura` a confirmar |
| `analytics.oee-cascada` | ✅ Resuelto (Fase I) |
| `analytics.rendimiento-secuencial` | ✅ Resuelto (Fase I) |
| `analytics.reporte-produccion` | ✅ Resuelto (Fase I) |
| `tenant.logo` | ✅ Resuelto (Fase J) — por URL, no upload |
| `usuarios.reset-password` | ⚠️ Contrato listo, `503` hasta configurar Auth0 M2M (Fase J) |
| `analytics.rendimiento-operarios` | ❌ No incluido en este ciclo (no estaba en el plan acordado) |
| `hub.api` | ❌ Fuera de alcance de este backend (producto aparte) |

Pueden sacar el `PreviewBadge`/`PendingBackendNotice` de todo lo marcado
✅ y reconectar los hooks reales.

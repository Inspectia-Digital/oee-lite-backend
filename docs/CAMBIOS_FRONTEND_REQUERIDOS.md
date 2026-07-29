# Cambios necesarios en el front — hardening production-grade

Este documento es para pedir/planificar el trabajo de front que soporte
todo lo desarrollado en el backend desde Fase A hasta la suite de tests
(commits `a023fb0`..`643a3f6` en `main`). Está ordenado por prioridad: lo
que hay que hacer **antes** de poder probar todo junto, lo que **rompe**
contratos existentes, y lo que son **pantallas nuevas** para funcionalidad
que hoy no tiene ningún lugar donde usarse.

Todos los endpoints están documentados de forma interactiva en
`https://api-dev.tymeo.inspectia.ai/docs` (Swagger UI) — no hace falta
adivinar payloads, se pueden probar ahí directamente con un token Bearer
de Gerencia/SuperAdmin.

---

## 0. Bloqueante — acción operativa, no requiere código de front

**`UsuarioPlanta` es una tabla nueva, sin ninguna asignación real todavía.**
Antes, un Supervisor/Operario accedía a las pantallas operativas
(`/supervisor/*`) con sólo tener cualquier planta seleccionada. Ahora se
exige que esté realmente asignado.

**Consecuencia:** cualquier supervisor/operario real que ya use esas
pantallas va a empezar a recibir `403` ("No tiene acceso a esta planta")
hasta que se lo asigne. Esto **no es un bug**, es la regla de negocio que
pedía el HANDOFF, pero hay que asignar a la gente **antes** de que alguien
pruebe y piense que se rompió todo.

Se puede hacer desde Swagger UI ahora mismo (no requiere pantalla nueva
para desbloquear la prueba, aunque sí conviene tener una pantalla
dedicada más adelante — ver sección 2):

```
POST /accesos/mi-empresa/usuario-planta
{"usuario_id": "<uuid del usuario>", "planta_id": "<uuid de la planta>"}
```

---

## 1. Cambios de contrato que ROMPEN algo existente

### 1.1 `PATCH /superadmin/tenants/{tenant_id}/estado`
- **Antes:** query param `?activo=true` / `?activo=false`.
- **Ahora:** body JSON `{"estado": "activo" | "ui_suspendida" | "suspension_total"}`.
- Si el front llama a este endpoint específico (probablemente sólo desde
  un panel de SuperAdmin), hay que actualizarlo. El endpoint viejo hacía
  algo distinto además: desactivaba usuarios uno por uno; el nuevo cambia
  el estado del tenant directamente, con semántica distinta (ver sección 3).

### 1.2 `POST /api/lite/scans` y `GET /api/lite/estaciones/{id}/validar` (Edge)
- **Antes:** autenticación con JWT humano de Auth0 (`Authorization: Bearer`).
- **Ahora:** autenticación M2M real, header `X-Device-Key: key_id.secret`.
  Ya no acepta JWT humano.
- **Esto no es un cambio de front web** — es un cambio de contrato para
  quien sea que hoy le mande datos a estos endpoints (terminales de
  Springwall, integración de Green Mills). Si hay algo real conectado hoy
  usando JWT humano, va a dejar de funcionar. Confirmado con el usuario
  que **hoy no hay hardware real conectado**, así que este cambio es
  seguro por ahora, pero cualquier desarrollo de terminal/Node-RED que se
  arranque de acá en más tiene que usar `X-Device-Key`, no un token de
  usuario.
- `POST /api/lite/scans` además ahora **exige** `event_id` (UUIDv4) en el
  body — antes no existía ese campo.

### 1.3 Cualquier `DELETE` administrativo pasó a ser baja lógica
- Antes (en varios recursos, ej. `DELETE /config/estaciones/{id}`,
  `DELETE /superadmin/usuarios/{auth0_id}`): borrado físico.
- Ahora: `activo`/`activa` pasa a `false`, la fila sigue existiendo.
- **Para el front:** un `GET` de detalle después de un `DELETE` ahora
  devuelve `200` con el registro (marcado inactivo) en vez de `404`. Si
  el front asumía "borrado = ya no existe", hay que ajustar esa lógica.
- Los listados (`GET`) excluyen inactivos por defecto — eso no cambió
  visualmente, pero ahora hay un query param nuevo: ver 2.1.

---

## 2. Pantallas nuevas necesarias (endpoints sin ningún lugar donde usarse hoy)

### 2.1 Filtro "ver inactivos" en los listados existentes
Casi todos los listados de maestros (`GET /config/lineas/`,
`/config/estaciones/`, `/config/motivos-parada/`, `/config/turnos/`,
`/config/erp/skus`, `/config/ordenes/`, `/config/operarios/`,
`/config/supervisores/`, `GET /accesos/mi-empresa/sub-tenants`) ahora
aceptan `?incluir_inactivos=true`. Sólo funciona para Gerencia/SuperAdmin
(403 si lo pide otro rol). Útil para una pantalla de administración que
quiera mostrar/reactivar elementos dados de baja.

### 2.2 Gestión de credenciales M2M (dispositivos)
Nuevo recurso completo, sin ninguna pantalla hoy:

```
POST   /config/api-keys/                    (emitir — el secret sólo se ve una vez)
GET    /config/api-keys/?estacion_id=...    (listar, nunca expone el secret)
POST   /config/api-keys/{id}/revocar
```

Necesita: pantalla para elegir una estación, emitir una key, mostrar el
`secret` completo **una única vez** con advertencia clara de que no se
puede volver a ver, y listar/revocar las existentes. Máximo 2 activas por
estación (el back devuelve `409` si se excede).

### 2.3 Asignación Usuario ↔ Planta
```
POST   /accesos/mi-empresa/usuario-planta
GET    /accesos/mi-empresa/usuario-planta?usuario_id=...&planta_id=...
DELETE /accesos/mi-empresa/usuario-planta/{id}
```
Sólo aplica a roles `SUPERVISOR` y `OPERARIO` (el back devuelve `400` si
se intenta asignar planta a Gerencia/SuperAdmin — no la necesitan, ven
todo el tenant). Es el bloqueante de la sección 0: conviene una pantalla
simple cuanto antes.

### 2.4 CRUD de Operarios y Supervisores
No existía ningún endpoint antes. Ahora completo:
```
POST/GET/PATCH/DELETE  /config/operarios/
POST/GET/PATCH/DELETE  /config/supervisores/
```
Legajo único por tenant entre activos (409 si se duplica; se puede
reutilizar después de una baja lógica).

### 2.5 CRUD de Órdenes de Producción
No existía ningún endpoint vivo antes (el único código que las tocaba no
estaba conectado a la aplicación). Ahora completo:
```
POST/GET/PATCH/DELETE  /config/ordenes/
```

### 2.6 Suspensión de tenant (panel SuperAdmin)
```
PATCH /accesos/superadmin/tenants/{tenant_id}/estado
{"estado": "activo" | "ui_suspendida" | "suspension_total"}
```
- `ui_suspendida`: bloquea pantallas humanas del tenant (403), pero el
  perfil propio (`/accesos/usuarios/me`) y el Edge (dispositivos) siguen
  funcionando.
- `suspension_total`: bloquea también el Edge.
- Sólo SuperAdmin puede cambiar el estado. Útil tener un selector claro
  con las 3 opciones y una confirmación (es una acción con impacto real
  para el cliente).

---

## 3. Nuevos códigos de estado que el front debería manejar explícitamente

| Código | Cuándo aparece | Sugerencia de UX |
|---|---|---|
| `403` con detalle "No tiene acceso a esta planta" | Supervisor/Operario sin `UsuarioPlanta` para la planta seleccionada | Mensaje claro, no un error genérico — indica que falta la asignación |
| `403` con detalle sobre suspensión | Tenant en `UI_SUSPENDIDA`/`SUSPENSION_TOTAL` | Banner de "cuenta suspendida", no un error de red |
| `409` en creación (líneas, SKU, orden, usuario-planta, operario, supervisor) | Duplicado / conflicto de unicidad | Mensaje del backend ya es descriptivo, se puede mostrar directo |
| `410 Gone` en `POST /supervisor/operarios/asignar-retroactivo` | Endpoint deshabilitado a propósito | Si el front todavía lo llama, hay que sacar esa función de la UI — nunca persistía nada, era un bug |

---

## 4. Campos nuevos en respuestas existentes (aditivo, no bloqueante)

No requieren cambios para que el front siga funcionando, pero conviene
actualizar tipos/interfaces si hay un cliente tipado:

- **Planta**: `timezone`, `activo`
- **Línea**: `metodo_calidad` (`por_tiempo`/`por_rechazo`), `activo`
- **Turno, Motivo de Parada, SKU, Orden, Operario, Supervisor**: `activo`
- **Tenant**: `estado` (`activo`/`ui_suspendida`/`suspension_total`)
- **`GET /analytics/oee-general/`**: `calidad_pct` ahora puede ser `null`
  (antes siempre era un número, incluso sin datos reales — significaba
  "sin datos suficientes para medir calidad", el front debería mostrar
  "N/D" en vez de "0%" o "100%" cuando sea `null`.

---

## 5. Qué NO cambió (para tranquilidad)

- Login humano (Auth0, JWT) sin cambios.
- Ningún endpoint de lectura existente perdió campos ni cambió tipos de
  los que ya devolvía.
- Los datos existentes (tenants, usuarios, plantas, líneas, estaciones,
  eventos históricos) no se tocaron ni se perdieron en ninguna migración.

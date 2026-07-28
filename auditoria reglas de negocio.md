# Catálogo de reglas de negocio deducidas del código

**Estado:** borrador para control cruzado con Negocio.  
**Fecha del relevamiento:** 28 de julio de 2026.  
**Propósito:** describir lo que el software parece asumir hoy, señalar contradicciones y obtener una definición formal antes de cambiar código.  
**Importante:** este documento **no modifica la aplicación**. Una regla marcada como “implementada” describe el comportamiento observado; no significa que Negocio la haya aprobado.

## Cómo revisar este documento

Cada regla tiene un identificador estable para que Negocio pueda responder sin ambigüedad:

- **I — Implementada:** comportamiento explícito y consistente observado en el código.
- **P — Parcial:** existe, pero sólo en algunos flujos o sin todas las validaciones esperables.
- **D — Deducida:** intención sugerida por nombres, defaults o comentarios; requiere confirmación.
- **C — Contradictoria:** dos partes del código representan reglas distintas o el modelo impide cumplir la intención.
- **N — No definida:** el código no permite deducir una decisión segura.

Para cada ID, Negocio debería marcar: **Aprobada**, **Modificar**, **Eliminar** o **Falta definición**, y escribir el texto definitivo, ejemplos y excepciones.

## 1. Glosario y estructura organizacional

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| ORG-001 | I | Un tenant representa una organización cliente y es la frontera principal de aislamiento de datos. | Confirmar que ninguna empresa puede compartir maestros ni transacciones con otra. |
| ORG-002 | C | El modelo admite tenants tipo `empresa` o `planta` y relación `parent_id`, pero la operación cotidiana modela plantas en una tabla `Planta` separada bajo un tenant empresa. | Elegir un único significado de “planta/sub-tenant”: tenant hijo, entidad física `Planta`, o ambos con funciones distintas. |
| ORG-003 | I | La jerarquía operativa observada es Tenant → Planta → Línea → Estación. | Confirmar obligatoriedad: hoy línea y estación pueden existir sin padre porque sus FK son opcionales. |
| ORG-004 | D | Una estación puede pertenecer a un ramal, tener posición secuencial y otra estación como padre. | Definir reglas de topología: unicidad de posición, ciclos prohibidos, cantidad de padres y significado de ramal. |
| ORG-005 | D | Una estación puede activarse/desactivarse sin borrarse. | Confirmar si una estación inactiva debe rechazar scans, desaparecer de reportes o conservar visualización histórica. Actualmente los scans no verifican `activa`. |
| ORG-006 | P | Varias pantallas operativas y analíticas requieren seleccionar una planta activa mediante `X-Sub-Tenant-Id`. | Definir qué módulos pueden funcionar en vista global y cuáles siempre requieren planta. |
| ORG-007 | P | Si el header de planta no es UUID, se busca por coincidencia parcial de nombre; si no existe, se elimina silenciosamente del contexto. | Confirmar si se admite nombre como identificador. Se recomienda ID exacto y error explícito para evitar seleccionar una planta equivocada. |

## 2. Tenancy, módulos y configuración de cliente

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| TEN-001 | I | Todo maestro y evento operativo debe estar asociado a `tenant_id`. | Aprobar como invariante obligatoria. |
| TEN-002 | P | Las consultas suelen filtrar por tenant, pero no todas las relaciones secundarias repiten el filtro. | Confirmar política “deny by default” y tests cruzados obligatorios. |
| TEN-003 | I | Un tenant inactivo o inexistente no puede usar módulos protegidos por contratación. | Definir si la inactividad también debe bloquear todos los endpoints que hoy sólo validan usuario. |
| TEN-004 | I | Los módulos contratados se almacenan como texto separado por comas y se comparan sin distinguir mayúsculas. La falta de un módulo devuelve HTTP 402. | Confirmar catálogo, dependencias entre módulos y si 403 sería semánticamente preferible. |
| TEN-005 | I | Sólo SuperAdmin puede impersonar otro tenant mediante query `tenant_id`. | Definir auditoría obligatoria, motivo, expiración y si debe existir un header/claim separado. |
| TEN-006 | D | El origen de maestros puede ser `MANUAL` o `ERP`. Cuando es `ERP`, la UI no permite cargas de plan/SKU. | Confirmar catálogo cerrado de orígenes y si se permiten excepciones de emergencia. |
| TEN-007 | I | Los defaults de tolerancia son 115% para lento y 125% para alerta respecto del ciclo ideal del SKU. | Confirmar valores, límites válidos y si aplican por tenant, línea, estación o SKU. |
| TEN-008 | D | El tenant define locale, tema, logo y color primario. | Definir formatos, tamaños y fallback; aclarar si son sólo presentación o afectan lógica/formato de fechas. |

## 3. Usuarios, identidad y RBAC

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| USR-001 | I | Auth0 autentica identidad; la aplicación autoriza sólo si existe un usuario local activo con el mismo `sub`. | Confirmar proceso de alta, sincronización, baja y recuperación ante desalineación Auth0/BD. |
| USR-002 | I | Roles disponibles: `SUPERADMIN`, `GERENCIA`, `PRODUCCION`, `SUPERVISOR`, `OPERARIO`. | Definir en lenguaje de negocio las responsabilidades y alcance por planta de cada rol. |
| USR-003 | P | Gerencia y SuperAdmin pueden crear usuarios del tenant; Gerencia no puede crear un SuperAdmin. | Confirmar si Gerencia puede crear otra Gerencia y administrar Supervisores de todas las plantas. |
| USR-004 | I | Supervisor, Gerencia y SuperAdmin pueden listar usuarios del tenant. | Confirmar si Supervisor debería ver email, `auth0_id`, estado y roles, o sólo datos operativos mínimos. |
| USR-005 | C | El update de usuarios del tenant no exige al inicio un rol administrador; sólo impide ciertos cambios propios o sobre SuperAdmin. | Definir matriz exacta y tratar como corrección prioritaria después de aprobación. |
| USR-006 | I | Un usuario no puede cambiar su propio rol mediante el endpoint del tenant; un SuperAdmin tampoco puede autodegradarse por el endpoint global. | Confirmar procedimiento de transferencia/revocación de SuperAdmin. |
| USR-007 | C | Los endpoints de alta generan un `auth0_id` mock local y no llaman necesariamente a Auth0 Management, por lo que el usuario creado podría no poder autenticarse. | Definir quién crea primero la identidad y cómo se compensan fallos parciales. |
| USR-008 | C | El endpoint global elimina físicamente usuarios, mientras el modelo tiene flag `activo`. | Elegir baja lógica, anonimización o borrado físico y definir retención/auditoría. |
| USR-009 | P | El “kill switch” de tenant cambia el estado de todos sus usuarios, pero no cambia `Tenant.activo`. | Definir si suspender empresa implica tenant inactivo, usuarios inactivos o ambos. |
| USR-010 | N | No hay reglas de alcance por planta para usuarios. | Definir si un usuario pertenece al tenant completo, a una o varias plantas, líneas o estaciones. |
| USR-011 | N | PLC/kioscos utilizan el contexto de autenticación existente sin un rol de dispositivo formal. | Definir identidad M2M, scopes, estación autorizada, rotación y revocación. |

## 4. Maestros de producción

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| MST-001 | I | Sólo Gerencia y SuperAdmin pueden crear líneas, estaciones, motivos y turnos por el router de configuración. | Confirmar si Producción o Supervisor pueden administrar algún maestro. |
| MST-002 | P | Crear línea exige una planta del mismo tenant. | Confirmar si toda línea debe tener planta; el modelo permite `planta_id` nulo. |
| MST-003 | P | Crear estación valida la línea cuando se informa, pero permite estación sin línea y no valida su `parent_id`. | Definir obligatoriedad y reglas para estaciones raíz/hijas. |
| MST-004 | I | Una estación tiene umbral óptimo, lento y alerta; defaults: 240, 280 y 300 segundos. | Definir invariantes `0 < óptimo ≤ lento ≤ alerta`, unidades y excepciones. |
| MST-005 | I | Modos de asignación de operario: línea `manual/escaneo`; estación `heredar/manual/escaneo`. | Definir precedencia, cambios durante un turno y comportamiento si falta asignación. |
| MST-006 | I | Tipos de producción: discreta (un ping = una unidad) y por lotes (un ping = factor del SKU). | Confirmar si pueden coexistir SKU/línea con modos diferentes y cómo manejar cambios históricos. |
| MST-007 | P | El legajo identifica operarios/supervisores, pero sólo tiene índice, no unicidad. | Definir unicidad global, por tenant o por planta y política de reutilización. |
| MST-008 | P | SKU incluye ciclo ideal, umbral de calidad, línea y unidades por ciclo. | Definir rangos, obligatoriedad, vigencia temporal y si un SKU puede estar en varias líneas. |
| MST-009 | C | `codigo_sku` es PK global aunque las consultas lo tratan como perteneciente a un tenant. | Confirmar si dos clientes pueden usar el mismo código; probablemente requiere identidad compuesta/UUID. |
| MST-010 | C | `id_orden` también es PK global aunque las órdenes se consultan por tenant. | Confirmar alcance del número de orden y reglas de unicidad. |
| MST-011 | I | Motivos de parada son `planificada` o `no_planificada`. | Definir catálogo, vigencia, jerarquías de causa y si un motivo puede cambiar de tipo tras ser usado. |
| MST-012 | P | El delete de estación es físico y se bloquea si hay escaneos/paradas asociadas. | Definir baja lógica y conservación histórica para todos los maestros. |

## 5. Planificación e integración ERP/importaciones

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| PLN-001 | I | Una orden tiene SKU, línea, cantidad esperada/producida, fecha planificada, estado y origen. | Definir obligatoriedad y transiciones válidas de estado. |
| PLN-002 | I | Estados modelados: abierta, en progreso y cerrada. | El webhook ERP intenta crear `PENDIENTE`, valor no incluido en el enum: definir estado inicial correcto. |
| PLN-003 | I | El webhook ERP hace upsert por número de orden y tenant; omite órdenes cuyo SKU no existe y reporta códigos ignorados. | Definir si la operación debe ser parcial, atómica o rechazar todo el lote. |
| PLN-004 | I | La carga de plan requiere planta seleccionada, línea perteneciente a esa planta y tenant no configurado como ERP exclusivo. | Confirmar roles autorizados: actualmente cualquier usuario autenticado que cumpla contexto puede cargar. |
| PLN-005 | I | Plan CSV/Excel requiere `id_orden`, `sku_fk`, `cantidad_esperada`, `plan_fecha`; puede renombrar columnas por JSON. | Definir formato de fecha, cantidad positiva, duplicados y máximo de filas/tamaño. |
| PLN-006 | I | Carga de SKUs requiere código y descripción; defaults: ciclo 240 s, unidades/ciclo 1. | Confirmar si aceptar defaults silenciosos o exigir datos explícitos. |
| PLN-007 | I | Las importaciones procesan filas individualmente, acumulan errores y hacen un commit final de las filas válidas. | Confirmar política de éxito parcial y formato de reporte/reintento. |
| PLN-008 | D | Origen de orden registra `CSV_TEMPLATE`, `CSV_MAPPED`, `UI` o ERP implícito. | Definir catálogo formal y auditoría de archivo/usuario. |
| PLN-009 | N | No hay regla explícita para reducir plan por debajo de cantidad ya producida, cerrar/reabrir orden o cambiar SKU con producción registrada. | Negocio debe definir estas transiciones. |

## 6. Captura de producción y trazabilidad

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| PRD-001 | I | Un scan debe referenciar una estación del mismo tenant. | Agregar decisión sobre estación activa y planta seleccionada. |
| PRD-002 | I | El timestamp puede venir del dispositivo; si falta, se usa hora UTC del servidor sin zona. | Definir tolerancia de reloj, timestamps futuros/antiguos, zona oficial y orden de eventos tardíos. |
| PRD-003 | P | Orden se toma de la orden activa de estación y puede reemplazarse con el primer grupo de una regex aplicada al código de pieza. | Definir precedencia, regex inválida, match sin grupo y qué hacer si la orden no existe. |
| PRD-004 | P | Existe `regex_parser_sku`, pero el flujo de scans no la utiliza; SKU proviene de `sku_activo_fk`. | Confirmar si el barcode debe resolver también SKU. |
| PRD-005 | I | Producción discreta suma 1 unidad; por lotes suma `unidades_por_ciclo` del SKU. El factor queda como snapshot en el evento. | Aprobar snapshot histórico y definir factor mínimo/máximo. |
| PRD-006 | I | Sin SKU activo se usan umbrales de estación; con SKU se usa ciclo teórico y tolerancias del tenant. | Definir precedencia exacta y si estación puede sobrescribir tolerancias de SKU. |
| PRD-007 | I | Primer evento de estación tiene delta 0 y estado `OPTIMO`. | Confirmar si debe excluirse de métricas de rendimiento. |
| PRD-008 | I | Delta mayor a alerta genera estado `ALERTA` y una parada pendiente desde el evento anterior hasta el actual. | Definir igualdad en límites (`>` actual), duración mínima/máxima y deduplicación. |
| PRD-009 | I | Cuando hay alerta, el delta persistido se recorta al tiempo óptimo para no penalizar rendimiento además de disponibilidad. | Confirmar criterio OEE; el delta real queda en la parada, no en el evento. |
| PRD-010 | I | Delta entre lento y alerta produce `LENTO`; de lo contrario `OPTIMO`. | Definir qué ocurre con delta negativo, cero o eventos fuera de orden. |
| PRD-011 | C | La búsqueda del último evento filtra estación pero no tenant; UUID hace improbable la colisión, pero contradice aislamiento estricto. | Aprobar filtro tenant obligatorio. |
| PRD-012 | N | No existe clave idempotente del dispositivo. | Definir tratamiento de reintentos/duplicados y orden concurrente. |
| PRD-013 | N | El scan no valida que orden/SKU activos existan, correspondan entre sí o pertenezcan a la línea. | Definir comportamiento: rechazar, advertir o registrar sin trazabilidad. |

## 7. Paradas y operación del supervisor

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| STP-001 | I | Una parada automática nace `pendiente`; al asignar motivo pasa a `clasificada`. | Definir reapertura, reclasificación, comentarios y responsable. |
| STP-002 | P | Listar pendientes requiere planta y filtra por tenant/planta; clasificar sólo valida tenant, no planta activa. | Definir si un supervisor puede clasificar en cualquier planta del tenant. |
| STP-003 | P | Cualquier motivo del mismo tenant puede clasificar una parada automática; no se restringe a no planificado. | Confirmar qué tipos son válidos para detección automática. |
| STP-004 | I | Una parada manual planificada requiere estación y motivo del tenant, motivo planificado y fin posterior al inicio; nace clasificada. | Agregar reglas de solapamiento, fechas futuras, turno y permisos. |
| STP-005 | C | La validación usa búsqueda textual de “planificada” sobre el enum; `no_planificada` también contiene esa cadena y podría aceptarse. | Definir comparación exacta obligatoria con `PLANIFICADA`. |
| STP-006 | N | No hay cancelación/anulación ni historial de cambios de parada. | Definir flujo auditable. |
| STP-007 | C | Asignación retroactiva declara actualizar eventos, pero `LiteEventoProduccion` no tiene `operario_fk`; puede informar actualizados sin persistir asignación. | Definir modelo y resultado correcto antes de habilitar la función. |
| STP-008 | P | Asignación retroactiva valida operario/tenant y filtra eventos por estación y rango, pero no valida estación ni rango positivo. | Definir límites temporales, sobrescritura y autorización. |

## 8. Turnos y asignación de personas

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| SHF-001 | I | Un turno tiene inicio, fin, descanso y opcionalmente línea. Si fin es anterior a inicio, Analytics lo interpreta como turno nocturno. | Confirmar si igualdad significa 0 o 24 horas y límites de descanso. |
| SHF-002 | I | La asignación vincula fecha, estación, operario y turno. | Definir unicidad, reemplazos, múltiples estaciones y supervisor responsable. |
| SHF-003 | N | No se controlan solapamientos de operario, estación o turno. | Definir restricciones y excepciones. |
| SHF-004 | C | El modo de asignación por escaneo/manual existe, pero el evento universal no conserva operario. | Definir trazabilidad laboral y migración necesaria. |

## 9. Reglas OEE y analítica

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| OEE-001 | I | OEE = Disponibilidad × Rendimiento × Calidad, cada factor limitado a máximo 100%. | Aprobar fórmula y redondeos. |
| OEE-002 | I | Tiempo planificado suma duración neta de turnos menos descansos por cantidad de días. Sin turnos usa 8 horas/día. | Confirmar fallback de 8 h y tratamiento de múltiples líneas/turnos superpuestos. |
| OEE-003 | I | Paradas planificadas se restan del tiempo planificado; no planificadas (y sin motivo) se restan del operativo. | Confirmar tratamiento de pendientes, solapamientos y paradas que cruzan el rango. |
| OEE-004 | I | Disponibilidad = operativo / planificado neto. Denominadores se fuerzan como mínimo a 1 segundo. | Confirmar si ausencia de tiempo debe devolver 0, N/A o 100%. |
| OEE-005 | P | Rendimiento usa suma de `umbral_optimo` de la estación por evento / tiempo operativo, sin multiplicar explícitamente por unidades de lote. | Confirmar fórmula correcta para lotes y SKU con ciclo variable. |
| OEE-006 | C | Calidad se calcula sólo en estaciones cuyo `tipo` textual sea “calidad”, como tiempo ideal / tiempo real; si no hay eventos de calidad devuelve 100%. | Confirmar si Calidad OEE debe ser unidades buenas / totales, y cómo tratar ausencia de control. |
| OEE-007 | P | Retrabajo se identifica por estado textual `RETRABAJO`, aunque el flujo principal de scans sólo genera `OPTIMO/LENTO/ALERTA`. | Definir origen y transición a retrabajo. |
| OEE-008 | C | El parámetro `fecha_hasta` está declarado en OEE general, pero el rango se construye sólo con `fecha_desde` y un día. | Definir rango inclusivo, zona horaria y límites. |
| OEE-009 | P | Dashboard agrupa por nombre de estación, no por ID; estaciones homónimas se fusionan. | Confirmar unicidad de nombre o agrupar por ID. |
| OEE-010 | P | Dashboard limita a 1000 eventos por defecto antes de agrupar. | Definir si debe paginar resultados agregados o calcular sobre todos los eventos. |
| OEE-011 | C | Errores internos de Analytics suelen convertirse en listas vacías o métricas cero. | Definir diferencia entre “sin datos” y “error de cálculo”; negocio no debería recibir cero falso. |
| OEE-012 | N | No hay política formal de zona horaria, calendario laboral, feriados, mantenimiento ni ventanas parciales. | Definirla antes de certificar indicadores. |

## 10. Reglas de calidad, alertas y cuello de botella

| ID | Estado | Regla observada/deducida | Confirmación solicitada |
|---|---|---|---|
| QLT-001 | D | Una estación con tipo textual `calidad` representa control de calidad. | Convertir tipos de estación a catálogo y definir múltiples controles. |
| QLT-002 | D | `umbral_calidad` del SKU existe, pero la fórmula OEE observada usa umbral óptimo de estación. | Definir cuál es la fuente correcta. |
| ALT-001 | D | Estados `LENTO` y `ALERTA` alimentan dashboard/alertas; una alerta también genera parada. | Definir ciclo de vida, reconocimiento, cierre y notificación. |
| BOT-001 | D | Cuello de botella se infiere comparando promedio real de delta contra umbral esperado por estación. | Definir muestra mínima, periodo, percentil vs promedio y desempate. |

## 11. Invariantes y validaciones que Negocio debe confirmar

1. IDs de SKU y orden: ¿únicos globalmente o sólo dentro del tenant?
2. Nombres de planta/línea/estación: ¿pueden repetirse y en qué alcance?
3. Umbrales: `óptimo > 0`, `lento ≥ óptimo`, `alerta ≥ lento`.
4. Unidades por ciclo y cantidades: enteros positivos; ¿se admite cero, merma o producción negativa por corrección?
5. Fechas: zona horaria por planta, tolerancia de reloj y cierre de día/turno.
6. Relaciones: planta obligatoria en línea, línea obligatoria en estación/SKU/turno.
7. Estados de orden y parada: transiciones permitidas, responsables y reversibilidad.
8. Bajas: preservar referencias históricas y evitar borrado físico de maestros usados.
9. Archivos: tamaño, filas, formatos, separadores, fórmulas, duplicados y atomicidad.
10. Auditoría: quién, cuándo, motivo, valor anterior/nuevo y origen para toda corrección.

## 12. Decisiones prioritarias para el taller con Negocio

### Prioridad 1 — bloquean integridad/seguridad

- Alcance exacto de tenant/planta y pertenencia de usuarios.
- Matriz RBAC definitiva, especialmente update de usuarios y cargas masivas.
- Identidad global o por tenant para SKU y orden.
- Identidad M2M de PLC/kioscos.
- Estados y transiciones de orden/parada.
- Idempotencia, timestamps tardíos y concurrencia de scans.

### Prioridad 2 — bloquean certificación OEE

- Fórmulas oficiales de Disponibilidad, Rendimiento y Calidad.
- Calendario, turnos, descansos, feriados y zonas horarias.
- Tratamiento de parada pendiente/planificada/solapada.
- Lotes, retrabajo, merma y correcciones.
- Comportamiento “sin datos” frente a error.

### Prioridad 3 — operación y mantenimiento

- Bajas lógicas y retención.
- Catálogo de estación/motivo/origen.
- Reglas de importación parcial/atómica.
- Alertas, escalamiento y cierre.

## 13. Plantilla de respuesta de Negocio

Completar una fila por regla que requiera decisión:

| ID | Decisión | Texto definitivo de la regla | Ejemplo válido | Ejemplo inválido | Excepciones | Responsable | Fecha |
|---|---|---|---|---|---|---|---|
| Ej. PRD-008 | Modificar | Una parada automática se crea cuando… | … | … | … | … | … |

Estados permitidos para **Decisión**: `Aprobada`, `Modificar`, `Eliminar`, `Falta definición`.

## 14. Criterio para convertir reglas aprobadas en código

Después de la aprobación funcional:

1. Consolidar las respuestas en una versión firmada del catálogo.
2. Crear trazabilidad Regla → endpoint/modelo/migración → caso de prueba.
3. Dividir cambios según los PR manuales definidos en `RELEVAMIENTO_CRUD_RBAC.md`.
4. Entregar archivos completos por PR con `MANIFEST.md`, `COMANDOS.md`, migración, pruebas y rollback.
5. No implementar una regla ambigua; devolverla a Negocio con ejemplos concretos.
6. Considerar conforme sólo cuando cada regla aprobada tenga pruebas positivas, negativas, RBAC y aislamiento multi-tenant.

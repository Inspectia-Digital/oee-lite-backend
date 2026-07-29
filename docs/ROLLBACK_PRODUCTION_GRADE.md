# Rollback — hardening/production-grade

## Código (Cloud Run)

Si el deploy a `dev` falla o rompe algo (login, CORS, endpoints):

1. Volver a desplegar la revisión anterior de Cloud Run (la que corresponde
   al commit `2579d39` de `main`, previo a este merge). Cloud Run mantiene
   revisiones previas: se puede redirigir el 100% del tráfico a la revisión
   anterior sin tocar la base de datos.
2. Esto es seguro incluso si ya se corrió la migración C1 (ver abajo):
   el código viejo simplemente ignora las columnas/tablas nuevas.

## Migración (Alembic) — sólo si hace falta revertir el esquema

**Fase C1 es expand-only: no borra nada legacy.** El downgrade es seguro
en el sentido de que no pierde datos *originales*, pero hay una excepción
a tener en cuenta:

```bash
python -m alembic downgrade 9261c6f3fe42
```

Efectos del downgrade:

- Elimina las tablas nuevas: `dim_maquinas`, `maquina_estacion`,
  `usuario_planta`, `api_keys_dispositivo` (y sus filas, si las hubiera).
- Elimina las columnas nuevas (`activo`, `timezone`, `metodo_calidad`,
  `estado`, `unidades_rechazadas`, `event_id`, `payload_hash`,
  `incluido_oee`, `id` en SKU/Orden) y sus índices.
- Vuelve a permitir `NULL` en `dim_lineas.planta_id` y
  `dim_estaciones.linea_id`.
- **NO elimina** las filas de "Planta Default" / "Línea Default" que la
  migración haya creado durante el saneamiento de huérfanos. Si para
  entonces ya se cargó producción real usando esas líneas/estaciones
  default, borrarlas manualmente causaría pérdida de datos. Revisar caso
  por caso antes de un downgrade en un entorno con datos reales.

Recomendación: en `dev`/`prod` con datos reales, preferir **roll-forward**
(corregir hacia adelante con una nueva migración) antes que downgrade,
salvo que se confirme que ninguna fila real depende de lo agregado.

## Migraciones D.1 y E2 — mismo criterio, expand puro

```bash
python -m alembic downgrade d40754a1ed06   # revierte E2 (tiempo_ideal_seg)
python -m alembic downgrade 9261c6f3fe42   # revierte también D.1 y C1
```

- `c941d511ae50` (D.1): elimina `usuario_planta.activo` y su índice único
  parcial. Si ya hay asignaciones usuario-planta reales cargadas, revisar
  antes: el downgrade no borra las filas de `usuario_planta`, sólo la
  columna `activo` (quedan sin forma de distinguir activas/inactivas hasta
  volver a migrar).
- `3dfb6eb20b53` (E2): elimina `lite_eventos_produccion.tiempo_ideal_seg`.
  Sin esta columna, el motor OEE (`analytics.py`) rompe al calcular
  Rendimiento — hacer downgrade de esta migración implica también revertir
  el código de Fase E2, no sólo el esquema.

## Fase C2 (contract) — no incluida en esta tanda

La fase C2 (retirar `codigo_sku`/`id_orden` como PK, retirar columnas
legacy, retirar `es_retrabajo`) todavía no existe. No hay nada que
revertir de C2 porque no se implementó.

## CI/CD — riesgo nuevo a tener en cuenta

Desde Fase D en adelante, `.github/workflows/deploy-dev.yml` corre la
migración **automáticamente** contra Cloud SQL real en cada push a `dev`
(o `workflow_dispatch`). Si un rollback de código implica también revertir
el esquema, hacerlo manualmente con los comandos de arriba **antes** de
que el próximo deploy automático vuelva a intentar migrar hacia adelante.

## Checklist antes de mergear a `dev`

- [ ] Backup/snapshot de Cloud SQL tomado.
- [ ] `AUTH0_DOMAIN` y `AUTH0_AUDIENCE` confirmados como variables de
      entorno explícitas en el servicio Cloud Run de `dev` (o, si no lo
      están, se acepta el fallback de transición que loguea warning —
      ver `app/core/auth.py`).
- [ ] `CORS_ORIGINS` confirmado con los orígenes reales del frontend, o
      se acepta el fallback permisivo de transición (loguea warning).
- [ ] Secret de GitHub `DEV_DATABASE_URL_TCP` configurado (necesario para
      que la migración automática de CI funcione).
- [ ] Login real probado post-deploy (usuario normal + superadmin).
- [ ] `/health/ready` del servicio responde 200 post-deploy.
- [ ] Supervisores/operarios reales asignados a su planta vía
      `POST /accesos/mi-empresa/usuario-planta` **antes** de que prueben
      las pantallas operativas (si no, van a recibir 403 nuevos — ver
      `docs/CAMBIOS_FRONTEND_REQUERIDOS.md`).

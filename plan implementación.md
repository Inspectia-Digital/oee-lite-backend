# Plan de implementación y promoción

Este plan permite revisar las mejoras en local antes de promoverlas. Se adapta al flujo solicitado `local → main → dev → prod`, aunque se recomienda confirmar los roles de ramas: normalmente `dev` integra primero y `main` representa lo liberable. Si se conserva el orden solicitado, proteger `main` y no interpretar un merge allí como aprobación productiva.

El relevamiento funcional de endpoints se mantiene por separado en [`RELEVAMIENTO_CRUD_RBAC.md`](RELEVAMIENTO_CRUD_RBAC.md). Sus endpoints son propuestas y deben dividirse en PR independientes sólo después de aprobación.

## 0. Preparar trazabilidad sin tocar ramas compartidas

1. Actualizar referencias y crear una rama desde el commit aprobado:

   ```bash
   git fetch --all --prune
   git switch main
   git pull --ff-only
   git switch -c audit/production-hardening
   ```

2. Registrar el SHA base con `git rev-parse HEAD` y mantener el árbol limpio con `git status --short`.
3. No copiar `.env`, tokens ni dumps reales al repositorio. Usar datos sintéticos o anonimizados.
4. Crear tickets para cada ID de auditoría (`SEC-01`, `TEN-01`, etc.). No agrupar todos los riesgos en un único merge difícil de revertir.

## 1. Rotar secretos antes de probar

Responsable: administrador Auth0/GCP, no sólo desarrollo.

1. Revocar la credencial hallada y el secreto M2M antiguo.
2. Crear credenciales nuevas con privilegios mínimos y guardarlas en Secret Manager/local `.env` ignorado.
3. Revisar eventos de uso desde la primera fecha versionada.
4. Ejecutar un escáner de secretos sobre árbol e historial.

**Gate 1:** evidencia de revocación y ausencia de secretos vigentes. Sin esto no se promueve ninguna rama.

## 2. Crear un entorno Python 3.11 realmente limpio

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
python -m compileall -q app main.py alembic
```

Después verificar importación con variables locales no sensibles:

```bash
export DATABASE_URL='postgresql+psycopg2://oeelite_user:oeelite_password@127.0.0.1:5432/oeelite_db'
export AUTH0_DOMAIN='tenant-de-prueba.us.auth0.com'
export AUTH0_AUDIENCE='https://api-local.example'
export CORS_ORIGINS='http://localhost:5173'
export ENVIRONMENT='development'
python -c 'from main import app; print(app.title)'
```

**Gate 2:** instalación desde cero e importación exitosa. Si falla, resolver el lock en un commit separado; no “arreglar” el virtualenv manualmente sin reflejarlo en dependencias.

## 3. Levantar PostgreSQL local y validar migraciones

```bash
docker compose up -d db
docker compose ps
alembic heads
alembic current
alembic upgrade head
alembic current
```

Pruebas obligatorias:

1. Base vacía: `upgrade head` funciona una vez.
2. Segunda ejecución: no cambia ni falla.
3. Copia anonimizada del schema actual: upgrade conserva filas y constraints.
4. Backup/restore: restaurar en otra base y repetir smoke tests.
5. Downgrade sólo si forma parte de la estrategia real; para cambios destructivos suele ser más seguro un roll-forward documentado.

**Gate 3:** reporte de migración con duración, conteos antes/después, revisión Alembic y procedimiento de recuperación.

## 4. Revisar el endurecimiento por commits pequeños

Orden recomendado para facilitar revisión y rollback:

1. `security: remove exposed credentials and bootstrap routes`
2. `config: validate production environment and CORS`
3. `auth: add timeouts and safe error responses`
4. `tenant: enforce hierarchy isolation and UUID contracts`
5. `database: disable runtime schema creation`
6. `build: normalize and lock Python dependencies`
7. `docs: add production runbook and audit evidence`

Para cada commit ejecutar `git show --stat`, revisar el diff y confirmar que no mezcla cambios funcionales no relacionados.

## 5. Incorporar tests antes de aceptar las correcciones

Crear como mínimo:

- `tests/test_config.py`: variables requeridas, normalización Auth0 y rechazo de `*` en producción.
- `tests/test_auth.py`: token inválido/expirado, JWKS caído, usuario inactivo y roles.
- `tests/test_tenant_isolation.py`: dos tenants con plantas/líneas/estaciones; lectura y escritura cruzada devuelven 403/404.
- `tests/test_scans_idempotency.py`: reintentos y concurrencia no duplican unidades.
- `tests/test_migrations.py`: base vacía y upgrade desde revisión soportada.
- `tests/test_removed_routes.py`: endpoints de emergencia devuelven 404.

Comando objetivo:

```bash
pytest -q --disable-warnings --maxfail=1
```

**Gate 4:** tests verdes dos veces desde base limpia; cobertura enfocada en reglas críticas, no sólo un porcentaje global.

## 6. Ejecutar la aplicación y smoke tests locales

```bash
uvicorn main:app --host 127.0.0.1 --port 8080
curl --fail http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8080/ascender-estanislao
curl -i -X POST http://127.0.0.1:8080/setup/primer-admin
```

Las dos últimas llamadas deben responder `404`. Probar además con tokens de usuario, gerente, superadmin y dispositivo; validar tenant incorrecto, planta incorrecta, uploads inválidos y Auth0 no disponible.

**Gate 5:** checklist firmado con respuestas esperadas y logs sin secretos/stack traces públicos.

## 7. Construir y probar exactamente el artefacto desplegable

```bash
docker build --pull -t inspectia-backend:local .
docker run --rm --env-file .env -p 8080:8080 inspectia-backend:local
docker history inspectia-backend:local
```

Escanear vulnerabilidades y SBOM con la herramienta corporativa. Confirmar usuario no root, ausencia de `.env`, proxy, dumps, scripts administrativos y credenciales dentro de las capas.

**Gate 6:** imagen reproducible, smoke tests contra la imagen y cero vulnerabilidades críticas sin excepción aprobada.

## 8. Promoción controlada de ramas

### Local → `main`

- Abrir PR desde `audit/production-hardening`.
- Exigir al menos una revisión técnica y una de seguridad/operaciones para SEC/DB.
- CI: instalación limpia, lint/compile, tests, migración efímera, secret scan, build y vulnerability scan.
- Merge sólo por commit/PR; prohibir push directo.

### `main` → `dev`

- Promover por PR usando el SHA o imagen ya construida, sin reconstruir dependencias distintas.
- Ejecutar migración en DB dev, smoke tests y pruebas multi-tenant.
- Observar métricas/logs al menos un ciclo operativo representativo.

### `dev` → `prod`

- Mantener `prod` vacía hasta cerrar todos los críticos/altos.
- Crear backup verificado, ventana de cambio, responsable de go/no-go y rollback.
- Promover la misma imagen por digest, ejecutar migración como job único y desplegar gradualmente.
- Validar readiness, errores, latencia e integridad de conteos antes de aumentar tráfico.

## Checklist de aprobación final

- [ ] SEC-01 y SEC-02 cerrados con evidencia.
- [ ] TEN-01, AUTH-01 y DATA-01 cubiertos por pruebas negativas.
- [ ] Migración probada desde snapshot y restauración verificada.
- [ ] Build Python 3.11 e imagen reproducibles.
- [ ] CI obligatorio y ramas protegidas.
- [ ] Readiness, logs, métricas y alertas activos.
- [ ] Runbook de incidentes, rollback y responsables definidos.
- [ ] Aprobación explícita de producto, desarrollo, seguridad y operaciones.

Si un gate falla, se corrige en la rama local y se repite desde ese gate; no se parchea manualmente una rama o entorno posterior.

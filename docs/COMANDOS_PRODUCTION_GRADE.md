# Comandos — hardening/production-grade

Todos probados localmente contra Postgres 15 en Docker (puerto 5433, para
no chocar con `cloud-sql-proxy.exe` que ya usa 5432 en esta máquina).

## 1. Entorno local

```bash
docker compose up -d db
```

```bash
export DATABASE_URL='postgresql+psycopg2://oeelite_user:oeelite_password@127.0.0.1:5433/oeelite_db'
export ENVIRONMENT='development'
```

```bash
python -m pip install -r requirements.txt
```

```bash
python -m pip check
```

```bash
python -m compileall -q app main.py alembic
```

## 2. Migración

```bash
python -m alembic heads
```

```bash
python -m alembic current
```

```bash
python -m alembic upgrade head
```

Verificado localmente (ver `docs/PR_PRODUCTION_GRADE.md` para el detalle):
base vacía, base con datos legacy con huérfanos insertados a mano, segunda
ejecución (no-op), downgrade + re-upgrade (reversible sin pérdida de datos).

## 3. Seed de datos de prueba (opcional, útil para smoke test manual)

```bash
python seed.py
```

## 4. Smoke test de la app

```bash
python -c "from main import app; print(app.title)"
```

```bash
uvicorn main:app --host 127.0.0.1 --port 8080
```

```bash
curl --fail http://127.0.0.1:8080/health/live
```

```bash
curl --fail http://127.0.0.1:8080/health/ready
```

```bash
curl -i http://127.0.0.1:8080/ruta-secreta
```

```bash
curl -i http://127.0.0.1:8080/ascender-estanislao
```

Las últimas dos deben devolver `404` (rutas de emergencia eliminadas).

## 5. Tests

```bash
python -m pytest -q
```

(No hay suite de tests todavía — QA-01 sigue abierto, es la fase pendiente
"test: add production-grade regression suite" del HANDOFF.)

## 6. Migración contra la base REAL de dev (Cloud SQL)

**No ejecutar sin backup previo.** El proxy `cloud-sql-proxy.exe` ya corre
en esta máquina apuntando a esa base (puerto 5432). Antes de migrar:

1. Confirmar que el PR ya fue mergeado a `dev` y que el código desplegado
   en Cloud Run corresponde a este mismo commit.
2. Tomar un snapshot/backup de la instancia Cloud SQL desde la consola de
   GCP (o `gcloud sql backups create`).
3. Con el proxy corriendo en 5432:

```bash
export DATABASE_URL='postgresql+psycopg2://<usuario_real>:<password_real>@127.0.0.1:5432/<db_real>'
```

```bash
python -m alembic current
```

```bash
python -m alembic upgrade head
```

4. Revisar la salida: debe imprimir cuántas filas de `maestro_skus`,
   `ordenes_produccion`, líneas y estaciones huérfanas fueron saneadas.
   Guardar esa salida como evidencia.
5. Verificar `/health/ready` del servicio desplegado y probar un login real.

## 7. Docker

```bash
docker build --pull -t oee-lite-backend:hardening .
```

```bash
docker run --rm --env-file .env -p 8080:8080 oee-lite-backend:hardening
```

## 8. Pendiente / no ejecutado en este entorno (warning explícito)

- `pip-audit` (SCA de dependencias): no está instalado en este entorno, no
  se instaló sin permiso. Pendiente de correr antes de producción real.
- Escaneo de secretos sobre el árbol/historial: pendiente (bloqueado por
  `auth.txt`/`cloud-sql-proxy.exe` aún trackeados, ver MANIFEST).

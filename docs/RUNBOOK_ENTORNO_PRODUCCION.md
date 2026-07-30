# Runbook — Entorno de producción separado (Fase L)

`deploy-prod.yml` ya está en el repo, pero no puedo ejecutar nada de esto
yo mismo: aprovisionar infraestructura real en tu proyecto GCP requiere
tus credenciales, no las mías. Esta es la checklist para dejarlo andando.

## 1. Cloud SQL — instancia de producción

```bash
gcloud sql instances create oeelite-db-prod \
  --project=oee-control \
  --database-version=POSTGRES_15 \
  --region=us-central1 \
  --tier=db-custom-1-3840 \
  --storage-size=20GB \
  --backup-start-time=03:00
```

- El tier es un punto de partida (1 vCPU / 3.75 GB) — ajustalo según carga
  esperada de Green Mills. `dev` corre en un contenedor local, no en Cloud
  SQL, así que no hay un tamaño de referencia previo para copiar.
- `--backup-start-time` habilita backups automáticos diarios. Sumale PITR
  si querés RPO más chico:
  ```bash
  gcloud sql instances patch oeelite-db-prod --enable-point-in-time-recovery
  ```

Crear la base y el usuario de la app dentro de la instancia:

```bash
gcloud sql databases create oeelite_db --instance=oeelite-db-prod
gcloud sql users create oeelite_user --instance=oeelite-db-prod --password="<elegí-una-password-fuerte>"
```

**Antes del go-live**, probá una restauración real una vez (auditoría QA
#20, versión mínima acordada -- no se pide automatizarlo, sólo verificar
que funciona):

```bash
gcloud sql backups list --instance=oeelite-db-prod
gcloud sql instances clone oeelite-db-prod oeelite-db-prod-restore-test \
  --point-in-time="<timestamp de un backup>"
# Verificar que oeelite-db-prod-restore-test tiene los datos esperados,
# después borrarla:
gcloud sql instances delete oeelite-db-prod-restore-test
```

## 2. Secretos en Google Secret Manager

`gcloud run deploy` usa `--set-secrets="DATABASE_URL=PROD_DATABASE_URL:latest"`
-- **`PROD_DATABASE_URL` tiene que existir como secreto de Secret Manager**
(no es un secreto de GitHub, es distinto de `PROD_DATABASE_URL_TCP` de
abajo). Formato con el conector nativo de Cloud SQL (socket Unix, sin
proxy):

```bash
echo -n "postgresql+psycopg2://oeelite_user:<password>@/oeelite_db?host=/cloudsql/oee-control:us-central1:oeelite-db-prod" | \
  gcloud secrets create PROD_DATABASE_URL --data-file=-
```

Dale acceso al service account que ya usa el deploy:

```bash
gcloud secrets add-iam-policy-binding PROD_DATABASE_URL \
  --member="serviceAccount:github-deploy-sa@oee-control.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## 3. Secretos en GitHub (Settings → Secrets and variables → Actions)

| Secret | Valor | Notas |
|---|---|---|
| `PROD_DATABASE_URL_TCP` | `oeelite_user:<password>@127.0.0.1:5432/oeelite_db` | Para el proxy TCP local que usa el job de migración (no confundir con el de arriba). |
| `PROD_CORS_ORIGINS` | `https://tu-dominio-real-del-front` | **Nunca `*`.** El workflow falla explícitamente si está vacío o es `*` -- no hace falta que confirmes el dominio ahora, sólo antes del primer deploy real. |
| `PROD_AUTH0_DOMAIN` | dominio de tu tenant Auth0 | Podés reusar el mismo valor que `AUTH0_DOMAIN` (dev) si vas a compartir el mismo tenant Auth0 entre ambientes, o uno nuevo si preferís separarlos. |
| `PROD_AUTH0_AUDIENCE` | audience de la API en Auth0 | Mismo criterio que el de arriba. |

`deploy-prod.yml` corta el deploy con un mensaje claro si falta cualquiera
de estos (antes de tocar nada en GCP).

## 4. Rama `prod`

El workflow dispara con push a la rama `prod` (o manualmente desde
Actions). Vos manejás git manualmente según lo acordado -- cuando quieras
el primer deploy real:

```bash
git checkout -b prod
git push origin prod
```

Los siguientes deploys a producción son merges a `prod` (o
`workflow_dispatch` manual).

## 5. Primer deploy — qué esperar

- El job `test` corre igual que en dev (Postgres real, pytest completo).
- El job `deploy` valida que los 4 secretos de arriba existan antes de
  tocar GCP.
- `ENVIRONMENT=production` activa los validadores de `Settings` en
  `app/core/config.py`: si falta `AUTH0_DOMAIN`/`AUTH0_AUDIENCE`/
  `CORS_ORIGINS` (o es `"*"`), la app ni arranca -- doble chequeo además
  del que ya hace el workflow.
- `--allow-unauthenticated` en Cloud Run es correcto (mismo criterio que
  dev): es el gate de IAM de Google, no la autenticación de la app. La
  seguridad real la sigue dando el JWT de Auth0 / `X-Device-Key`.
- `--min-instances 1` (vs. 0 en dev) para evitar cold-start en el primer
  cliente real.

## 6. Pendiente, no bloqueante para el primer deploy

- Alertas/monitoring sobre el Cloud Run y Cloud SQL de prod (uptime
  checks, alertas de error rate) -- pospuesto con el resto de
  observabilidad avanzada (Fase K lo dejó explícitamente para después).
- Decidir si Auth0 de prod es el mismo tenant que dev o uno separado --
  no bloquea el primer deploy, sólo cambia qué valores van en
  `PROD_AUTH0_DOMAIN`/`PROD_AUTH0_AUDIENCE`.

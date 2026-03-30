#!/usr/bin/env bash
# Deploy crawler containers on Hetzner (worker machine).
# Postgres runs on a separate dedicated machine.
# Called by CI with env vars set from GitHub secrets.
set -euo pipefail

# ── Validate required env vars ─────────────────────────────────────────
required_vars=(
  OWNER
  DATABASE_URL_UNPOOLED
  LOCAL_DATABASE_URL
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_ENDPOINT_URL
  R2_DOMAIN_URL
  R2_BUCKET
  GRAFANA_PROM_URL
  GRAFANA_PROM_USERNAME
  GRAFANA_PROM_PASSWORD
  GRAFANA_LOKI_URL
  GRAFANA_LOKI_USERNAME
  GRAFANA_LOKI_PASSWORD
)

missing=()
for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("$var")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: Missing required env vars: ${missing[*]}" >&2
  exit 1
fi

DEPLOY_DIR="/home/deploy"

# ── Write env file ──────────────────────────────────────────────────
cat > "$DEPLOY_DIR/.env" <<EOF
OWNER=${OWNER}
DATABASE_URL=${DATABASE_URL_UNPOOLED}
LOCAL_DATABASE_URL=${LOCAL_DATABASE_URL}
R2_ACCESS_KEY_ID=${R2_ACCESS_KEY_ID}
R2_SECRET_ACCESS_KEY=${R2_SECRET_ACCESS_KEY}
R2_ENDPOINT_URL=${R2_ENDPOINT_URL}
R2_DOMAIN_URL=${R2_DOMAIN_URL}
R2_BUCKET=${R2_BUCKET}
GRAFANA_PROM_URL=${GRAFANA_PROM_URL}
GRAFANA_PROM_USERNAME=${GRAFANA_PROM_USERNAME}
GRAFANA_PROM_PASSWORD=${GRAFANA_PROM_PASSWORD}
GRAFANA_LOKI_URL=${GRAFANA_LOKI_URL}
GRAFANA_LOKI_USERNAME=${GRAFANA_LOKI_USERNAME}
GRAFANA_LOKI_PASSWORD=${GRAFANA_LOKI_PASSWORD}
EOF

# ── Pull images and restart ──────────────────────────────────────────
cd "$DEPLOY_DIR"
docker compose pull
docker compose up -d --remove-orphans

# ── Run Alembic migrations on local Postgres ─────────────────────────
docker compose exec worker uv run --no-sync alembic -c src/migrations/alembic.ini upgrade head

# ── Sync board config from CSV → local Postgres + Redis ──────────────
docker compose exec worker uv run --no-sync crawler sync

# ── Cleanup ──────────────────────────────────────────────────────────
docker image prune -f
echo "Deploy complete: $(docker compose ps --format '{{.Name}}' | tr '\n' ' ')"

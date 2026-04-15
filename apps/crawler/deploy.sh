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
  TYPESENSE_HOST
  TYPESENSE_PORT
  TYPESENSE_PROTOCOL
  TYPESENSE_ADMIN_KEY
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

# ── Stop any manually-started containers that conflict with compose ──
docker rm -f redis worker-1 worker-2 worker-3 browser-1 exporter drain alloy 2>/dev/null || true

# ── Write env file ──────────────────────────────────────────────────
# Proxy vars are expanded with ``:-`` defaults so missing provider
# secrets don't break the deploy — PROXY_PROVIDER=none disables the
# proxy layer even when the URL envs are empty.
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
TYPESENSE_HOST=${TYPESENSE_HOST}
TYPESENSE_PORT=${TYPESENSE_PORT}
TYPESENSE_PROTOCOL=${TYPESENSE_PROTOCOL}
TYPESENSE_ADMIN_KEY=${TYPESENSE_ADMIN_KEY}
PROXY_PROVIDER=${PROXY_PROVIDER:-none}
WEBSHARE_PROXY_URL=${WEBSHARE_PROXY_URL:-}
DECODO_PROXY_URL=${DECODO_PROXY_URL:-}
EOF

# ── Pull images and restart ──────────────────────────────────────────
cd "$DEPLOY_DIR"
docker compose pull

# Keep Redis up for migrations + sync, but quiesce the rest of the
# crawler so deploy-time `crawler sync` does not race with live workers
# claiming work out of Redis while we are reseeding board monitors.
docker compose stop worker-1 worker-2 worker-3 browser-1 exporter drain alloy 2>/dev/null || true
docker compose up -d redis

# ── Run Alembic migrations on local Postgres ─────────────────────────
docker run --rm --env-file "$DEPLOY_DIR/.env" --network host \
  "ghcr.io/${OWNER}/jobseek-crawler:latest" \
  uv run --no-sync alembic -c src/migrations/alembic.ini upgrade head

# ── Sync board config from CSV → local Postgres + Redis ──────────────
docker run --rm --env-file "$DEPLOY_DIR/.env" --network host \
  "ghcr.io/${OWNER}/jobseek-crawler:latest" \
  uv run --no-sync crawler sync

# ── Start the full stack on the freshly seeded Redis state ───────────
docker compose up -d --remove-orphans

# ── Cleanup ──────────────────────────────────────────────────────────
docker image prune -f
echo "Deploy complete: $(docker compose ps --format '{{.Name}}' | tr '\n' ' ')"

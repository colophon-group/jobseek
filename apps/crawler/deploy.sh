#!/usr/bin/env bash
# Deploy crawler containers on Hetzner.
# Called by CI with env vars set from GitHub secrets.
#
# Required env vars:
#   OWNER              — GitHub repository owner (for image names)
#   DATABASE_URL       — Postgres connection string
#   UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN
#   R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL,
#   R2_DOMAIN_URL, R2_BUCKET
set -euo pipefail

OWNER="${OWNER:?OWNER env var required}"
DEPLOY_DIR="/home/deploy"

# ── Write per-worker env files ────────────────────────────────────────

cat > "$DEPLOY_DIR/crawler-common.env" <<EOF
DATABASE_URL=${DATABASE_URL}
WORKER_ID_PREFIX=hetzner
LOG_LEVEL=INFO
CRAWLER_DB_POOL_MAX=10
EOF

cat > "$DEPLOY_DIR/crawler-http.env" <<EOF
UPSTASH_REDIS_REST_URL=${UPSTASH_REDIS_REST_URL}
UPSTASH_REDIS_REST_TOKEN=${UPSTASH_REDIS_REST_TOKEN}
EOF

cat > "$DEPLOY_DIR/crawler-r2.env" <<EOF
R2_ACCESS_KEY_ID=${R2_ACCESS_KEY_ID}
R2_SECRET_ACCESS_KEY=${R2_SECRET_ACCESS_KEY}
R2_ENDPOINT_URL=${R2_ENDPOINT_URL}
R2_DOMAIN_URL=${R2_DOMAIN_URL}
R2_BUCKET=${R2_BUCKET}
EOF

# ── Pull images ───────────────────────────────────────────────────────

docker pull "ghcr.io/$OWNER/jobseek-crawler-http:latest"
docker pull "ghcr.io/$OWNER/jobseek-crawler-browser:latest"
docker pull "ghcr.io/$OWNER/jobseek-crawler-r2-drain:latest"

# ── Stop old containers ───────────────────────────────────────────────

docker stop crawler-http crawler-browser crawler-r2-drain 2>/dev/null || true
docker rm   crawler-http crawler-browser crawler-r2-drain 2>/dev/null || true

# ── Start new containers ──────────────────────────────────────────────

docker run -d --name crawler-http \
  --restart unless-stopped \
  --env-file "$DEPLOY_DIR/crawler-common.env" \
  --env-file "$DEPLOY_DIR/crawler-http.env" \
  --network host --memory=2g --cpus=1.5 \
  -e CRAWLER_MAX_CONCURRENT=20 -e CRAWLER_MAX_BROWSER=0 \
  "ghcr.io/$OWNER/jobseek-crawler-http:latest"

docker run -d --name crawler-browser \
  --restart unless-stopped \
  --env-file "$DEPLOY_DIR/crawler-common.env" \
  --env-file "$DEPLOY_DIR/crawler-http.env" \
  --network host --memory=3g --shm-size=1g --cpus=1.0 \
  -e CRAWLER_MAX_CONCURRENT=0 -e CRAWLER_MAX_BROWSER=2 -e METRICS_PORT=9092 \
  "ghcr.io/$OWNER/jobseek-crawler-browser:latest"

docker run -d --name crawler-r2-drain \
  --restart unless-stopped \
  --env-file "$DEPLOY_DIR/crawler-common.env" \
  --env-file "$DEPLOY_DIR/crawler-r2.env" \
  --network host --memory=256m --cpus=1.0 \
  -e METRICS_PORT=9093 \
  "ghcr.io/$OWNER/jobseek-crawler-r2-drain:latest"

# ── Cleanup ───────────────────────────────────────────────────────────

docker image prune -f
echo "Deploy complete: $(docker ps --format '{{.Names}}' | tr '\n' ' ')"

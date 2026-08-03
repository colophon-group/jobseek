#!/usr/bin/env bash
# Restore the latest encrypted web-owned logical backup into clean PostgreSQL.
set -euo pipefail

IMAGE="${WEB_POSTGRES_IMAGE:-postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193}"
ENV_FILE="${WEB_POSTGRES_BACKUP_ENV_FILE:-/etc/jobseek-backup/web-postgresql.env}"
STATUS_FILE="${WEB_POSTGRES_RESTORE_STATUS_FILE:-/var/lib/jobseek-backup/status/web-postgresql-restore.json}"
DRILL_ROOT="${WEB_POSTGRES_DRILL_ROOT:-/run/jobseek-backup/web-postgresql/drills}"
CONTAINER="jobseek-web-postgresql-restore-${RANDOM}-$$"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STARTED_UNIX="$(date +%s)"
SUCCESS=false
TABLE_COUNT=0
ROW_COUNT=0
ARCHIVE_SHA256=""
RESTORE_PATH=""

write_status() {
  local finished_unix duration
  finished_unix="$(date +%s)"
  duration="$((finished_unix - STARTED_UNIX))"
  install -d -m 0700 "$(dirname "$STATUS_FILE")"
  STARTED_AT="$STARTED_AT" \
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  DURATION="$duration" \
  SUCCESS="$SUCCESS" \
  TABLE_COUNT="$TABLE_COUNT" \
  ROW_COUNT="$ROW_COUNT" \
  ARCHIVE_SHA256="$ARCHIVE_SHA256" \
  STATUS_FILE="$STATUS_FILE" \
  python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["STATUS_FILE"])
record = {
    "schema_version": 1,
    "service": "web-postgresql-restore",
    "started_at": os.environ["STARTED_AT"],
    "finished_at": os.environ["FINISHED_AT"],
    "duration_seconds": int(os.environ["DURATION"]),
    "success": os.environ["SUCCESS"] == "true",
    "table_count": int(os.environ["TABLE_COUNT"]),
    "row_count": int(os.environ["ROW_COUNT"]),
    "archive_sha256": os.environ["ARCHIVE_SHA256"],
}
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.chmod(0o600)
os.replace(temporary, path)
PY
}

cleanup() {
  local exit_code=$?
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  if [[ -n "$RESTORE_PATH" ]]; then
    rm -rf -- "$RESTORE_PATH"
  fi
  write_status
  trap - EXIT
  exit "$exit_code"
}
trap cleanup EXIT

[[ "$IMAGE" == *@sha256:* ]] || {
  echo "ERROR: WEB_POSTGRES_IMAGE must be digest-pinned" >&2
  exit 1
}
test -s "$ENV_FILE"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"
: "${RESTIC_SFTP_COMMAND:?RESTIC_SFTP_COMMAND is required}"

exec 9>/run/jobseek-data-backup-web-postgresql.lock
flock -n 9 || {
  echo "ERROR: web PostgreSQL backup or restore is already running" >&2
  exit 1
}

install -d -m 0700 "$DRILL_ROOT"
RESTORE_PATH="$(mktemp -d "$DRILL_ROOT/restore.XXXXXX")"
restic -o "sftp.command=${RESTIC_SFTP_COMMAND}" restore latest \
  --tag jobseek-web-postgresql \
  --host jobseek-web-postgresql \
  --target "$RESTORE_PATH"

mapfile -t dumps < <(find "$RESTORE_PATH" -type f -name web-postgresql.dump -print)
mapfile -t manifests < <(find "$RESTORE_PATH" -type f -name manifest.json -print)
mapfile -t bootstraps < <(find "$RESTORE_PATH" -type f -name bootstrap.sql -print)
[[ ${#dumps[@]} -eq 1 && ${#manifests[@]} -eq 1 && ${#bootstraps[@]} -eq 1 ]] || {
  echo "ERROR: restored snapshot must contain exactly one dump, manifest, and bootstrap" >&2
  exit 1
}
DUMP_PATH="${dumps[0]}"
MANIFEST_PATH="${manifests[0]}"
BOOTSTRAP_PATH="${bootstraps[0]}"
ARCHIVE_DIR="$(dirname "$DUMP_PATH")"
[[ "$MANIFEST_PATH" == "$ARCHIVE_DIR/manifest.json" && "$BOOTSTRAP_PATH" == "$ARCHIVE_DIR/bootstrap.sql" ]] || {
  echo "ERROR: dump, manifest, and bootstrap are not from the same backup run" >&2
  exit 1
}

docker run --detach --rm \
  --name "$CONTAINER" \
  --pull=never \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_HOST_AUTH_METHOD=trust \
  --env POSTGRES_DB=web_restore \
  --tmpfs /var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=512m \
  --volume "$ARCHIVE_DIR:/restore:ro" \
  "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready -U postgres -d web_restore >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U postgres -d web_restore >/dev/null
docker exec "$CONTAINER" psql \
  --no-psqlrc --set ON_ERROR_STOP=1 --quiet \
  --username postgres --dbname web_restore \
  --file /restore/bootstrap.sql
docker exec "$CONTAINER" pg_restore \
  --exit-on-error \
  --no-owner \
  --no-privileges \
  --username postgres \
  --dbname web_restore \
  /restore/web-postgresql.dump

PORT_LINE="$(docker port "$CONTAINER" 5432/tcp)"
PORT="${PORT_LINE##*:}"
[[ "$PORT" =~ ^[0-9]+$ ]] || {
  echo "ERROR: could not resolve the loopback restore port" >&2
  exit 1
}
WEB_DATABASE_URL="postgresql://postgres@127.0.0.1:${PORT}/web_restore" \
  /usr/local/sbin/jobseek-data-backup web-postgresql-verify \
  --manifest "$MANIFEST_PATH" \
  --dump "$DUMP_PATH" \
  --bootstrap "$BOOTSTRAP_PATH"

# Exercise the restored Better Auth, watchlist, saved-job/interview, followed
# company, request, and outreach write constraints without retaining fixtures.
docker exec -i "$CONTAINER" psql \
  --no-psqlrc --set ON_ERROR_STOP=1 --quiet \
  --username postgres --dbname web_restore <<'SQL'
BEGIN;
INSERT INTO "user" (id, name, email) VALUES
  ('restore-smoke-user', 'Restore Smoke', 'restore-smoke@invalid.example');
INSERT INTO session (id, expires_at, token, updated_at, user_id) VALUES
  ('restore-smoke-session', now() + interval '1 hour', 'restore-smoke-token', now(), 'restore-smoke-user');
INSERT INTO account (id, account_id, provider_id, user_id, updated_at) VALUES
  ('restore-smoke-account', 'restore-smoke', 'credential', 'restore-smoke-user', now());
INSERT INTO verification (id, identifier, value, expires_at) VALUES
  ('restore-smoke-verification', 'restore-smoke', 'value', now() + interval '1 hour');
INSERT INTO user_preferences (user_id) VALUES ('restore-smoke-user');
INSERT INTO company (id, name, slug) VALUES
  ('00000000-0000-0000-0000-000000000101', 'Restore Smoke', 'restore-smoke-company');
INSERT INTO job_board (id, company_id, board_url) VALUES
  ('00000000-0000-0000-0000-000000000102', '00000000-0000-0000-0000-000000000101', 'https://invalid.example/restore-smoke');
INSERT INTO saved_job (
  id,
  user_id,
  job_posting_id,
  posting_title,
  posting_source_url,
  posting_first_seen_at,
  posting_is_active,
  company_id,
  company_name,
  company_slug
) VALUES (
  '00000000-0000-0000-0000-000000000103',
  'restore-smoke-user',
  '00000000-0000-0000-0000-000000000104',
  'Restore Smoke',
  'https://invalid.example/restore-smoke/job',
  now(),
  true,
  '00000000-0000-0000-0000-000000000101',
  'Restore Smoke',
  'restore-smoke-company'
);
INSERT INTO application_interview (id, saved_job_id, round, type) VALUES
  ('00000000-0000-0000-0000-000000000105', '00000000-0000-0000-0000-000000000103', 1, 'interview');
INSERT INTO followed_company (user_id, company_id) VALUES
  ('restore-smoke-user', '00000000-0000-0000-0000-000000000101');
INSERT INTO watchlist (id, user_id, slug, title) VALUES
  ('00000000-0000-0000-0000-000000000106', 'restore-smoke-user', 'restore-smoke', 'Restore Smoke');
INSERT INTO watchlist_company (watchlist_id, company_id) VALUES
  ('00000000-0000-0000-0000-000000000106', '00000000-0000-0000-0000-000000000101');
INSERT INTO company_request (input, resolved_company_id, resolved_job_board_id) VALUES
  ('restore-smoke.invalid', '00000000-0000-0000-0000-000000000101', '00000000-0000-0000-0000-000000000102');
INSERT INTO hiring_signal (id, company_id, signal_type, signal_text, signal_date, source_id) VALUES
  ('00000000-0000-0000-0000-000000000107', '00000000-0000-0000-0000-000000000101', 'restore', 'Restore smoke', now(), 'restore-smoke');
INSERT INTO outreach_draft (signal_id, contact_name, subject, body) VALUES
  ('00000000-0000-0000-0000-000000000107', 'Restore Smoke', 'Restore Smoke', 'Restore Smoke');
ROLLBACK;
SQL

read -r TABLE_COUNT ROW_COUNT ARCHIVE_SHA256 < <(
  python3 - "$MANIFEST_PATH" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
fingerprints = manifest["fingerprints"]
print(len(fingerprints), sum(item["rows"] for item in fingerprints.values()), manifest["archive_sha256"])
PY
)
SUCCESS=true
echo "Web PostgreSQL restore drill passed: tables=${TABLE_COUNT} rows=${ROW_COUNT}"

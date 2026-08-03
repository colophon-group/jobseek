#!/usr/bin/env bash
# Restore the latest encrypted web-owned logical backup into clean PostgreSQL.
set -euo pipefail

IMAGE="${WEB_POSTGRES_IMAGE:-postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193}"
ENV_FILE="${WEB_POSTGRES_BACKUP_ENV_FILE:-/etc/jobseek-backup/web-postgresql.env}"
STATUS_FILE="${WEB_POSTGRES_RESTORE_STATUS_FILE:-/var/lib/jobseek-backup/status/web-postgresql-restore.json}"
DRILL_ROOT="${WEB_POSTGRES_DRILL_ROOT:-/run/jobseek-backup/web-postgresql/drills}"
OPERATION_ID="${WEB_POSTGRES_RESTORE_OPERATION_ID:-}"
if [[ -z "$OPERATION_ID" ]]; then
  OPERATION_ID="$(python3 - <<'PY'
import secrets

print(secrets.token_hex(16))
PY
)"
fi
[[ "$OPERATION_ID" =~ ^[0-9a-f]{32}$ ]] || {
  echo "ERROR: invalid restore operation ID" >&2
  exit 2
}
EXPECTED_CONTAINER="jobseek-web-postgresql-restore-${OPERATION_ID}"
CONTAINER="${WEB_POSTGRES_RESTORE_CONTAINER:-$EXPECTED_CONTAINER}"
NETWORK="${WEB_POSTGRES_RESTORE_NETWORK:-${EXPECTED_CONTAINER}-network}"
OPERATION_ROOT="${WEB_POSTGRES_RESTORE_OPERATION_ROOT:-$DRILL_ROOT/operation-${OPERATION_ID}}"
[[ "$CONTAINER" == "$EXPECTED_CONTAINER" ]]
[[ "$NETWORK" == "${EXPECTED_CONTAINER}-network" ]]
[[ "$OPERATION_ROOT" == "$DRILL_ROOT/operation-${OPERATION_ID}" ]]
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STARTED_UNIX="$(date +%s)"
SUCCESS=false
TABLE_COUNT=0
ROW_COUNT=0
ARCHIVE_SHA256=""
RESTORE_PATH=""
CREDENTIAL_PATH=""

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

docker_resource_absent() {
  local kind="$1" name="$2"
  if docker "$kind" inspect "$name" >/dev/null 2>&1; then
    return 1
  fi
  # Distinguish an absent resource from an inspect failure caused by an
  # unavailable daemon. Only a reachable daemon plus failed inspect proves
  # that the named resource no longer exists.
  docker info --format '{{.ServerVersion}}' >/dev/null 2>&1
}

cleanup() {
  local exit_code=$? cleanup_failed=false
  trap - EXIT HUP INT TERM
  set +e
  docker rm -f "$CONTAINER" >/dev/null 2>&1
  if ! docker_resource_absent container "$CONTAINER"; then
    cleanup_failed=true
  fi
  docker network rm "$NETWORK" >/dev/null 2>&1
  if ! docker_resource_absent network "$NETWORK"; then
    cleanup_failed=true
  fi
  if [[ -n "$RESTORE_PATH" ]]; then
    rm -rf -- "$RESTORE_PATH" || cleanup_failed=true
  fi
  if [[ -n "$CREDENTIAL_PATH" ]]; then
    rm -rf -- "$CREDENTIAL_PATH" || cleanup_failed=true
  fi
  if [[ -n "$OPERATION_ROOT" ]]; then
    rm -rf -- "$OPERATION_ROOT" || cleanup_failed=true
  fi
  if [[ "$exit_code" -ne 0 || "$cleanup_failed" == true ]]; then
    SUCCESS=false
    exit_code=1
  fi
  if ! write_status; then
    exit_code=1
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

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

if [[ -n "${WEB_POSTGRES_RESTORE_LOCK_FD:-}" || \
  -n "${WEB_POSTGRES_RESTORE_DEPLOYMENT_LOCK_FD:-}" ]]; then
  [[ "${WEB_POSTGRES_RESTORE_LOCK_FD:-}" =~ ^[0-9]+$ ]]
  [[ "${WEB_POSTGRES_RESTORE_DEPLOYMENT_LOCK_FD:-}" =~ ^[0-9]+$ ]]
  [[ "$(readlink "/proc/$$/fd/${WEB_POSTGRES_RESTORE_LOCK_FD:-}")" == \
    /run/jobseek-data-backup-web-postgresql.lock ]]
  [[ "$(readlink "/proc/$$/fd/${WEB_POSTGRES_RESTORE_DEPLOYMENT_LOCK_FD:-}")" == \
    /run/jobseek-backup-deployment.lock ]]
  flock -n "${WEB_POSTGRES_RESTORE_LOCK_FD:-}"
  flock -n "${WEB_POSTGRES_RESTORE_DEPLOYMENT_LOCK_FD:-}"
else
  exec 8>/run/jobseek-backup-deployment.lock
  flock -n 8 || {
    echo "ERROR: another backup deployment or protected operation is active" >&2
    exit 1
  }
  exec 9>/run/jobseek-data-backup-web-postgresql.lock
  flock -n 9 || {
    echo "ERROR: web PostgreSQL backup or restore is already running" >&2
    exit 1
  }
fi

install -d -m 0700 "$DRILL_ROOT"
mkdir -m 0700 "$OPERATION_ROOT"
RESTORE_PATH="$OPERATION_ROOT/restore"
mkdir -m 0700 "$RESTORE_PATH"
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

CREDENTIAL_PATH="$OPERATION_ROOT/credential"
mkdir -m 0700 "$CREDENTIAL_PATH"
PASSWORD_FILE="$CREDENTIAL_PATH/postgres-password"
PGPASS_FILE="$CREDENTIAL_PATH/pgpass"
python3 - "$PASSWORD_FILE" "$PGPASS_FILE" <<'PY'
import secrets
import sys
from pathlib import Path

password = secrets.token_hex(32)
password_path = Path(sys.argv[1])
pgpass_path = Path(sys.argv[2])
password_path.write_text(password + "\n", encoding="utf-8")
pgpass_path.write_text(f"*:*:*:postgres:{password}\n", encoding="utf-8")
password_path.chmod(0o600)
pgpass_path.chmod(0o600)
PY
docker network create --driver bridge --internal \
  --label jobseek.backup.service=web-postgresql-restore \
  --label "jobseek.backup.operation=$OPERATION_ID" \
  "$NETWORK" >/dev/null
docker run --detach --rm \
  --name "$CONTAINER" \
  --label jobseek.backup.service=web-postgresql-restore \
  --label "jobseek.backup.operation=$OPERATION_ID" \
  --pull=never \
  --network "$NETWORK" \
  --env POSTGRES_PASSWORD_FILE=/run/secrets/postgres-password \
  --env POSTGRES_DB=web_restore \
  --tmpfs /var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=512m \
  --volume "$PASSWORD_FILE:/run/secrets/postgres-password:ro" \
  --volume "$PGPASS_FILE:/run/secrets/web-database-pgpass:ro" \
  --volume "$ARCHIVE_DIR:/restore:ro" \
  "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready \
      --host=127.0.0.1 --username=postgres --dbname=web_restore >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$CONTAINER" pg_isready \
  --host=127.0.0.1 --username=postgres --dbname=web_restore >/dev/null
docker exec "$CONTAINER" sh -ceu '
  export PGPASSFILE=/run/secrets/web-database-pgpass
  exec psql --no-psqlrc --set ON_ERROR_STOP=1 --quiet \
    --host=127.0.0.1 --username=postgres --dbname=web_restore \
    --file /restore/bootstrap.sql
'
docker exec "$CONTAINER" sh -ceu '
  export PGPASSFILE=/run/secrets/web-database-pgpass
  exec pg_restore --exit-on-error --no-owner --no-privileges \
    --host=127.0.0.1 --username=postgres --dbname=web_restore \
    /restore/web-postgresql.dump
'

WEB_DATABASE_PASSWORD_FILE="$PGPASS_FILE" \
WEB_DATABASE_HOST="$CONTAINER" \
WEB_DATABASE_PORT=5432 \
WEB_DATABASE_USER=postgres \
WEB_DATABASE_NAME=web_restore \
WEB_POSTGRES_NETWORK="$NETWORK" \
  /usr/local/sbin/jobseek-data-backup web-postgresql-verify \
  --manifest "$MANIFEST_PATH" \
  --dump "$DUMP_PATH" \
  --bootstrap "$BOOTSTRAP_PATH"

# Exercise the restored Better Auth, watchlist, saved-job/interview, followed
# company, request, and outreach write constraints without retaining fixtures.
docker exec -i "$CONTAINER" sh -ceu '
  export PGPASSFILE=/run/secrets/web-database-pgpass
  exec psql --no-psqlrc --set ON_ERROR_STOP=1 --quiet \
    --host=127.0.0.1 --username=postgres --dbname=web_restore
' <<'SQL'
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

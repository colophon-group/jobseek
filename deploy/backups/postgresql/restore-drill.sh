#!/usr/bin/env bash
# Restore the latest production backup into a disposable, network-isolated
# PostgreSQL container and validate every heap and B-tree relation.
set -euo pipefail

IMAGE="${JOBSEEK_POSTGRES_BACKUP_IMAGE:-jobseek-postgres:16-pgbackrest}"
CONFIG_DIR="${JOBSEEK_POSTGRES_BACKUP_CONFIG:-/etc/jobseek-backup/postgresql}"
REPOSITORY_DIR="${JOBSEEK_POSTGRES_BACKUP_REPOSITORY:-/mnt/jobseek-postgresql-backups}"
BASE_DIR="${JOBSEEK_POSTGRES_RESTORE_DRILL_DIR:-/var/lib/jobseek-backup/postgresql/restore-drills}"
KEEP_FAILED="${JOBSEEK_POSTGRES_RESTORE_KEEP_FAILED:-false}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)${RANDOM}"
CONTAINER="jobseek-postgresql-restore-${RUN_ID}"
DATA_DIR="$BASE_DIR/$RUN_ID"
SPOOL_DIR="$BASE_DIR/${RUN_ID}-spool"

cleanup() {
  local rc=$?
  trap - EXIT
  if [[ "$rc" -ne 0 ]]; then
    docker inspect "$CONTAINER" --format \
      'restore_container status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{.State.Error}}' \
      2>/dev/null || true
    docker logs --tail 100 "$CONTAINER" 2>/dev/null || true
    if [[ "$KEEP_FAILED" == true ]]; then
      echo "Retained failed restore drill data for diagnosis: $DATA_DIR" >&2
      exit "$rc"
    fi
  fi
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  [[ "$DATA_DIR" == "$BASE_DIR"/* ]]
  [[ "$SPOOL_DIR" == "$BASE_DIR"/*-spool ]]
  rm -rf "$DATA_DIR" "$SPOOL_DIR"
  exit "$rc"
}
trap cleanup EXIT

test -s "$CONFIG_DIR/pgbackrest.conf"
mountpoint -q "$REPOSITORY_DIR"
[[ "$(findmnt -n -o FSTYPE "$REPOSITORY_DIR")" == cifs ]]
docker image inspect "$IMAGE" >/dev/null
docker container inspect "$CONTAINER" >/dev/null 2>&1 && {
  echo "Restore drill container already exists: $CONTAINER" >&2
  exit 1
}
install -d -o 70 -g 70 -m 0700 "$DATA_DIR" "$SPOOL_DIR"

docker run --rm \
  --network none \
  --user 70:70 \
  --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
  --volume "$CONFIG_DIR/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro" \
  --volume "$REPOSITORY_DIR:$REPOSITORY_DIR" \
  --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
  --volume "$DATA_DIR:/restore" \
  "$IMAGE" \
  pgbackrest --stanza=jobseek --pg1-path=/restore restore

docker run --detach \
  --name "$CONTAINER" \
  --network none \
  --memory 4g \
  --restart no \
  --env PGDATA=/restore \
  --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
  --volume "$CONFIG_DIR/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro" \
  --volume "$REPOSITORY_DIR:$REPOSITORY_DIR" \
  --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
  --volume "$DATA_DIR:/restore" \
  "$IMAGE" \
  postgres \
    -c 'listen_addresses=' \
    -c 'archive_mode=off' \
    -c 'logging_collector=off' \
    -c 'log_destination=stderr' >/dev/null

for _ in $(seq 1 600); do
  if docker exec "$CONTAINER" pg_isready -U crawler -d crawler >/dev/null 2>&1 && \
    [[ "$(docker exec "$CONTAINER" psql -U crawler -d crawler -Atc \
      'select pg_is_in_recovery()' 2>/dev/null)" == f ]]; then
    break
  fi
  if [[ "$(docker inspect "$CONTAINER" --format '{{.State.Running}}')" != true ]]; then
    echo "Restore drill PostgreSQL exited before becoming ready" >&2
    exit 1
  fi
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U crawler -d crawler >/dev/null
readiness="$(docker exec "$CONTAINER" psql -U crawler -d crawler -v ON_ERROR_STOP=1 -Atc \
  "select current_setting('server_version'), pg_is_in_recovery(), current_setting('archive_mode')")"
[[ "$readiness" == "16.13|f|off" ]]

docker exec "$CONTAINER" pg_amcheck \
  --username=crawler \
  --database=crawler \
  --install-missing \
  --jobs=1 \
  --on-error-stop \
  --parent-check

docker exec "$CONTAINER" psql -U crawler -d crawler -v ON_ERROR_STOP=1 -Atc \
  "select 'database_bytes=' || pg_database_size(current_database());
   select 'public_tables=' || count(*) from information_schema.tables
     where table_schema = 'public' and table_type = 'BASE TABLE';
   select 'companies=' || count(*) from company;
   select 'job_boards=' || count(*) from job_board;
   select 'job_postings=' || count(*) from job_posting;
   select 'active_job_postings=' || count(*) from job_posting where is_active;
   select 'invalid_job_postings=' || count(*) from job_posting
     where id is null or company_id is null or board_id is null or source_url is null;"

echo "PostgreSQL production backup restore and relation integrity drill passed"

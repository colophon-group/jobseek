#!/usr/bin/env bash
# Replace the current PostgreSQL 16 container with the pgBackRest-capable image.
set -euo pipefail

CURRENT_NAME="postgres"
ROLLBACK_NAME="postgres-rollback-pre-pgbackrest"
CURRENT_IMAGE="postgres:16-alpine"
TARGET_IMAGE="jobseek-postgres:16-pgbackrest"
DATA_DIR="/mnt/HC_Volume_105256309/pgdata"
CONFIG_DIR="/etc/jobseek-backup/postgresql"
SPOOL_DIR="/var/lib/jobseek-backup/postgresql/spool"

usage() {
  echo "Usage: $0 <apply|rollback|finalize>" >&2
}

wait_ready() {
  local attempts=0
  until docker exec "$CURRENT_NAME" pg_isready -U crawler -d crawler >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [[ "$attempts" -ge 60 ]]; then
      return 1
    fi
    sleep 1
  done
}

rollback() {
  if docker container inspect "$ROLLBACK_NAME" >/dev/null 2>&1; then
    docker rm -f "$CURRENT_NAME" >/dev/null 2>&1 || true
    docker rename "$ROLLBACK_NAME" "$CURRENT_NAME"
    docker start "$CURRENT_NAME" >/dev/null
    wait_ready
    echo "Restored the original PostgreSQL container"
  elif docker container inspect "$CURRENT_NAME" >/dev/null 2>&1; then
    docker start "$CURRENT_NAME" >/dev/null
    wait_ready
    echo "Restarted the unchanged PostgreSQL container"
  else
    echo "Neither the current nor rollback PostgreSQL container exists" >&2
    return 1
  fi
}

apply() {
  test -s "$CONFIG_DIR/postgres.env"
  test -s "$CONFIG_DIR/pgbackrest.conf"
  test -s "$CONFIG_DIR/id_ed25519"
  test -d "$DATA_DIR"
  test -d "$SPOOL_DIR"
  docker image inspect "$TARGET_IMAGE" >/dev/null
  [[ "$(docker inspect "$CURRENT_NAME" --format '{{.Config.Image}}')" == "$CURRENT_IMAGE" ]]
  if docker container inspect "$ROLLBACK_NAME" >/dev/null 2>&1; then
    echo "Refusing to overwrite existing rollback container $ROLLBACK_NAME" >&2
    return 1
  fi

  local current_version target_version
  current_version="$(docker exec "$CURRENT_NAME" postgres --version)"
  target_version="$(docker run --rm --entrypoint postgres "$TARGET_IMAGE" --version)"
  [[ "$target_version" == "$current_version" ]]

  # Validate SFTP, host fingerprint, key access, and repository encryption
  # before changing the live server. Stanza creation itself happens in the
  # replacement container, where PostgreSQL's Unix socket is available.
  docker run --rm \
    --user 70:70 \
    --network host \
    --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
    --volume "$CONFIG_DIR/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro" \
    --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
    "$TARGET_IMAGE" \
    pgbackrest --stanza=jobseek repo-ls >/dev/null

  local rollback_needed=1
  # rc is assigned when the ERR trap executes.
  # shellcheck disable=SC2154
  trap 'rc=$?; if [[ "$rollback_needed" -eq 1 ]]; then rollback_needed=0; rollback || true; fi; exit "$rc"' ERR

  docker stop --time 60 "$CURRENT_NAME" >/dev/null
  docker rename "$CURRENT_NAME" "$ROLLBACK_NAME"
  rm -f "$SPOOL_DIR/archive-enabled"

  docker run --detach \
    --name "$CURRENT_NAME" \
    --network host \
    --memory 4g \
    --restart unless-stopped \
    --env-file "$CONFIG_DIR/postgres.env" \
    --volume "$DATA_DIR:/var/lib/postgresql/data" \
    --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
    --volume "$CONFIG_DIR/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro" \
    --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
    "$TARGET_IMAGE" \
    postgres \
      -c 'listen_addresses=*' \
      -c 'max_connections=100' \
      -c 'shared_buffers=1GB' \
      -c 'work_mem=16MB' \
      -c 'wal_level=replica' \
      -c 'max_wal_senders=3' \
      -c 'max_wal_size=4GB' \
      -c 'min_wal_size=1GB' \
      -c 'checkpoint_timeout=15min' \
      -c 'checkpoint_completion_target=0.9' \
      -c 'wal_compression=on' \
      -c 'archive_mode=on' \
      -c 'archive_command=test -f /var/spool/pgbackrest/archive-enabled && pgbackrest --stanza=jobseek archive-push %p' \
      -c 'archive_timeout=60s' >/dev/null

  wait_ready
  docker exec --user postgres "$CURRENT_NAME" pgbackrest --stanza=jobseek stanza-create
  docker exec --user postgres "$CURRENT_NAME" touch /var/spool/pgbackrest/archive-enabled
  docker exec --user postgres "$CURRENT_NAME" pgbackrest --stanza=jobseek check
  docker exec "$CURRENT_NAME" psql -U crawler -d crawler -v ON_ERROR_STOP=1 -Atc \
    "select current_setting('server_version'), current_setting('archive_mode'), current_setting('wal_level'), current_setting('max_wal_senders'), current_setting('max_wal_size'), current_setting('shared_buffers')"

  rollback_needed=0
  trap - ERR
  echo "PostgreSQL migration succeeded; retain $ROLLBACK_NAME until restore validation passes"
}

case "${1:-}" in
  apply)
    apply
    ;;
  rollback)
    rollback
    ;;
  finalize)
    [[ "$(docker inspect "$CURRENT_NAME" --format '{{.Config.Image}}')" == "$TARGET_IMAGE" ]]
    [[ "$(docker inspect "$ROLLBACK_NAME" --format '{{.State.Running}}')" == "false" ]]
    docker rm "$ROLLBACK_NAME" >/dev/null
    echo "Removed validated PostgreSQL rollback container"
    ;;
  *)
    usage
    exit 2
    ;;
esac

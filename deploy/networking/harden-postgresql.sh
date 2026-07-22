#!/usr/bin/env bash
# Replace PostgreSQL with a private-bind/HBA-constrained equivalent and retain rollback.
set -euo pipefail

ACTION="${1:-}"
CRAWLER_PRIVATE_IP="${JOBSEEK_CRAWLER_PRIVATE_IP:-}"
POSTGRES_PRIVATE_IP="${JOBSEEK_POSTGRES_PRIVATE_IP:-}"
CURRENT_NAME=postgres
ROLLBACK_NAME=postgres-rollback-pre-ingress
TARGET_IMAGE=jobseek-postgres:16-pgbackrest
CONFIG_DIR=/etc/jobseek-backup/postgresql
SPOOL_DIR=/var/lib/jobseek-backup/postgresql/spool
REPOSITORY_DIR=/mnt/jobseek-postgresql-backups
STATE_ROOT=/var/lib/jobseek-ingress/postgresql
ROLLBACK_ROOT="${STATE_ROOT}/rollback"
PENDING_FILE="${STATE_ROOT}/pending"
NETWORK_CONFIG=/etc/jobseek-ingress/postgresql-network.env
INSTALLED=/usr/local/sbin/jobseek-postgresql-network
ROLLBACK_UNIT=jobseek-postgresql-network-rollback
ROLLBACK_ARMED=0

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ "$ACTION" =~ ^(stage|commit|rollback)$ ]] || {
  echo "Usage: $0 <stage|commit|rollback>" >&2
  exit 2
}
[[ "$(id -u)" -eq 0 ]] || fail "must run as root"
for value in "$CRAWLER_PRIVATE_IP" "$POSTGRES_PRIVATE_IP"; do
  [[ "$value" =~ ^10\.|^192\.168\.|^172\.(1[6-9]|2[0-9]|3[01])\. ]] ||
    fail "private IPv4 values are required"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || fail "invalid private IPv4 value"
done

install_layout() {
  install -d -o root -g root -m 0700 "$STATE_ROOT" "$ROLLBACK_ROOT"
  if [[ "$(readlink -f "${BASH_SOURCE[0]}")" != "$INSTALLED" ]]; then
    install -o root -g root -m 0755 "${BASH_SOURCE[0]}" "$INSTALLED"
  fi
}

cancel_rollback_timer() {
  # Never stop the service here: automatic rollback executes inside that
  # service, and stopping itself would terminate the restore mid-transaction.
  systemctl stop "${ROLLBACK_UNIT}.timer" >/dev/null 2>&1 || true
  systemctl reset-failed "${ROLLBACK_UNIT}.service" >/dev/null 2>&1 || true
}

wait_ready() {
  local attempts=0
  until docker exec "$CURRENT_NAME" pg_isready -U crawler -d crawler >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    [[ "$attempts" -lt 60 ]] || return 1
    sleep 1
  done
}

data_dir() {
  docker inspect "$CURRENT_NAME" --format \
    '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}'
}

validate_backup() {
  mountpoint -q "$REPOSITORY_DIR" || fail "PostgreSQL backup repository is not mounted"
  python3 - <<'PY'
import json
import time
from pathlib import Path

status = json.loads(Path("/var/lib/jobseek-backup/status/postgresql.json").read_text())
fresh = time.time() - float(status.get("last_success_unix") or 0) <= 48 * 3600
if status.get("success") is not True or not fresh:
    raise SystemExit("a fresh successful PostgreSQL backup is required")
PY
}

validate_container_contract() {
  [[ "$(docker inspect "$CURRENT_NAME" --format '{{.Config.Image}}')" == "$TARGET_IMAGE" ]] ||
    fail "unexpected PostgreSQL image"
  [[ "$(docker inspect "$CURRENT_NAME" --format '{{.HostConfig.NetworkMode}}')" == host ]] ||
    fail "PostgreSQL must use host networking"
  [[ "$(docker inspect "$CURRENT_NAME" --format '{{.HostConfig.Memory}}')" == 4294967296 ]] ||
    fail "unexpected PostgreSQL memory limit"
  test -s "$CONFIG_DIR/postgres.env"
  test -s "$CONFIG_DIR/pgbackrest.conf"
  test -d "$SPOOL_DIR"
  test -d "$(data_dir)"
}

snapshot_hba() {
  local directory="$1" source owner mode
  source="$(data_dir)/pg_hba.conf"
  test -s "$source"
  cp -a "$source" "$directory/pg_hba.conf"
  owner="$(stat -c '%u:%g' "$source")"
  mode="$(stat -c '%a' "$source")"
  printf '%s\n' "$owner" >"$directory/hba-owner"
  printf '%s\n' "$mode" >"$directory/hba-mode"
  if [[ -f "$NETWORK_CONFIG" ]]; then
    cp -a "$NETWORK_CONFIG" "$directory/postgresql-network.env"
  else
    touch "$directory/network-config-absent"
  fi
}

write_network_config() {
  install -d -o root -g root -m 0700 /etc/jobseek-ingress
  local temporary
  temporary="$(mktemp /etc/jobseek-ingress/.postgresql-network.env.XXXXXX)"
  printf 'JOBSEEK_POSTGRES_LISTEN_ADDRESSES=127.0.0.1,%s\n' "$POSTGRES_PRIVATE_IP" >"$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  mv "$temporary" "$NETWORK_CONFIG"
}

write_hba() {
  local source owner mode uid gid temporary
  source="$(data_dir)/pg_hba.conf"
  owner="$(stat -c '%u:%g' "$source")"
  mode="$(stat -c '%a' "$source")"
  uid="${owner%%:*}"
  gid="${owner##*:}"
  temporary="$(mktemp "$(dirname "$source")/.pg_hba.conf.XXXXXX")"
  cat >"$temporary" <<EOF
# Managed by colophon-group/jobseek deploy/networking/harden-postgresql.sh.
# The Hetzner private path is isolated and source-restricted. TLS is not
# required by the current policy; SCRAM remains mandatory for every TCP path.
local all all trust
host crawler crawler 127.0.0.1/32 scram-sha-256
host crawler jobseek_labeller_readonly 127.0.0.1/32 scram-sha-256
host crawler crawler ::1/128 scram-sha-256
host crawler jobseek_labeller_readonly ::1/128 scram-sha-256
host crawler crawler ${CRAWLER_PRIVATE_IP}/32 scram-sha-256
host crawler jobseek_labeller_readonly ${CRAWLER_PRIVATE_IP}/32 scram-sha-256
EOF
  chown "$uid:$gid" "$temporary"
  chmod "$mode" "$temporary"
  mv "$temporary" "$source"
}

restore_hba() {
  local tx="$1" target owner mode uid gid
  target="$(data_dir)/pg_hba.conf"
  owner="$(cat "$tx/hba-owner")"
  mode="$(cat "$tx/hba-mode")"
  uid="${owner%%:*}"
  gid="${owner##*:}"
  install -o "$uid" -g "$gid" -m "$mode" "$tx/pg_hba.conf" "$target"
  if [[ -f "$tx/postgresql-network.env" ]]; then
    install -o root -g root -m 0600 "$tx/postgresql-network.env" "$NETWORK_CONFIG"
  else
    rm -f "$NETWORK_CONFIG"
  fi
}

arm_rollback_timer() {
  cancel_rollback_timer
  systemd-run \
    --unit="$ROLLBACK_UNIT" \
    --on-active=15m \
    --property=Type=oneshot \
    --setenv="JOBSEEK_CRAWLER_PRIVATE_IP=${CRAWLER_PRIVATE_IP}" \
    --setenv="JOBSEEK_POSTGRES_PRIVATE_IP=${POSTGRES_PRIVATE_IP}" \
    "$INSTALLED" rollback >/dev/null
}

rollback() {
  local tx
  [[ -s "$PENDING_FILE" ]] || fail "no pending PostgreSQL network transaction"
  tx="$(cat "$PENDING_FILE")"
  [[ "$tx" == "$ROLLBACK_ROOT"/* && -s "$tx/pg_hba.conf" ]] || fail "invalid rollback state"
  cancel_rollback_timer
  if docker container inspect "$ROLLBACK_NAME" >/dev/null 2>&1; then
    docker rm -f "$CURRENT_NAME" >/dev/null 2>&1 || true
    # The rollback container keeps the original listener command and mounts.
    docker rename "$ROLLBACK_NAME" "$CURRENT_NAME"
    restore_hba "$tx"
    docker start "$CURRENT_NAME" >/dev/null
    wait_ready
  else
    restore_hba "$tx"
    if [[ "$(docker inspect "$CURRENT_NAME" --format '{{.State.Running}}')" == true ]]; then
      docker exec "$CURRENT_NAME" psql -U crawler -d crawler -Atc 'select pg_reload_conf()' >/dev/null
    else
      docker start "$CURRENT_NAME" >/dev/null
      wait_ready
    fi
  fi
  rm -f "$PENDING_FILE"
  echo "Restored the previous PostgreSQL network policy"
}

rollback_on_error() {
  local status=$?
  trap - ERR EXIT
  if [[ "$ROLLBACK_ARMED" == 1 ]]; then
    rollback || true
  fi
  exit "$status"
}

run_replacement() {
  local data="$1"
  [[ -d "$data" ]] || fail "PostgreSQL data mount disappeared"
  docker run --detach \
    --name "$CURRENT_NAME" \
    --network host \
    --memory 4g \
    --restart unless-stopped \
    --env-file "$CONFIG_DIR/postgres.env" \
    --volume "$data:/var/lib/postgresql/data" \
    --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
    --volume "$CONFIG_DIR/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro" \
    --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
    --volume "$REPOSITORY_DIR:$REPOSITORY_DIR" \
    "$TARGET_IMAGE" \
    postgres \
      -c "listen_addresses=127.0.0.1,${POSTGRES_PRIVATE_IP}" \
      -c 'password_encryption=scram-sha-256' \
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
}

verify_local() {
  local settings
  wait_ready
  settings="$(
    docker exec "$CURRENT_NAME" psql -U crawler -d crawler -v ON_ERROR_STOP=1 -Atc \
      "select current_setting('listen_addresses'), current_setting('password_encryption'), current_setting('archive_mode')"
  )"
  grep -Fqx "127.0.0.1,${POSTGRES_PRIVATE_IP}|scram-sha-256|on" <<<"$settings"
  docker exec --user postgres "$CURRENT_NAME" pgbackrest --stanza=jobseek check >/dev/null
}

stage() {
  [[ ! -e "$PENDING_FILE" ]] || fail "a PostgreSQL network transaction is already pending"
  ! docker container inspect "$ROLLBACK_NAME" >/dev/null 2>&1 || fail "rollback container already exists"
  validate_backup
  validate_container_contract
  local stamp tx data
  data="$(data_dir)"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  tx="${ROLLBACK_ROOT}/${stamp}"
  install -d -o root -g root -m 0700 "$tx"
  snapshot_hba "$tx"
  printf '%s\n' "$tx" >"${PENDING_FILE}.tmp"
  chmod 0600 "${PENDING_FILE}.tmp"
  mv "${PENDING_FILE}.tmp" "$PENDING_FILE"
  ROLLBACK_ARMED=1
  trap rollback_on_error ERR EXIT
  arm_rollback_timer
  write_network_config
  write_hba
  docker stop --time 60 "$CURRENT_NAME" >/dev/null
  docker rename "$CURRENT_NAME" "$ROLLBACK_NAME"
  run_replacement "$data"
  verify_local
  ROLLBACK_ARMED=0
  trap - ERR EXIT
  echo "Staged private-only PostgreSQL listener; rollback container and timer retained"
}

commit() {
  verify_local
  [[ -s "$PENDING_FILE" ]] || fail "no pending PostgreSQL network transaction"
  [[ "$(docker inspect "$ROLLBACK_NAME" --format '{{.State.Running}}')" == false ]]
  cancel_rollback_timer
  docker rm "$ROLLBACK_NAME" >/dev/null
  rm -f "$PENDING_FILE"
  find "$ROLLBACK_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf -- {} +
  echo "Committed private-only PostgreSQL listener"
}

install_layout
case "$ACTION" in
  stage) stage ;;
  commit) commit ;;
  rollback) rollback ;;
esac

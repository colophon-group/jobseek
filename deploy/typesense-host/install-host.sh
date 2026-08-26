#!/usr/bin/env bash
# Install the credential-safe Typesense and Cloudflare Tunnel host surface.
set -euo pipefail

usage() {
  echo "Usage: $0 <all|typesense|cloudflared>" >&2
}

COMPONENT="${1:-all}"
case "$COMPONENT" in
  all|typesense|cloudflared) ;;
  *)
    usage
    exit 2
    ;;
esac

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: install-host.sh must run as root" >&2
  exit 1
fi
: "${JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA:?Exact host deploy SHA is required}"
[[ "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR=/var/lib/jobseek-typesense-host
CREDENTIAL_DIR=/etc/jobseek-typesense
TYPESENSE_CONFIG="$CREDENTIAL_DIR/typesense-server.ini"
CLOUDFLARED_TOKEN_FILE="$CREDENTIAL_DIR/cloudflare-tunnel-token"
CLOUDFLARED_UNIT=/etc/systemd/system/cloudflared.service
TYPESENSE_IMAGE=typesense/typesense:27.1@sha256:5c12af89130b8ee0be11541321ba8a3a7c7a538d7c6cd95e0409dc2d75ca6455
TYPESENSE_DATA_DIR=/mnt/typesense-data
TYPESENSE_SNAPSHOT_DIR=/mnt/jobseek-typesense-backup
TYPESENSE_CONFIG_IN_CONTAINER=/run/secrets/typesense-server.ini
TYPESENSE_SNAPSHOT_IN_CONTAINER=/jobseek-snapshots
TYPESENSE_NOFILE_LIMIT=65536
TYPESENSE_LOG_MAX_SIZE=50m
TYPESENSE_LOG_MAX_FILES=3
TYPESENSE_MEMORY_LIMIT=6g
TYPESENSE_MEMORY_RESERVATION=5g
TYPESENSE_MEMORY_SWAP=6g
TYPESENSE_MEMORY_LIMIT_BYTES=6442450944
TYPESENSE_MEMORY_RESERVATION_BYTES=5368709120
TYPESENSE_MEMORY_SWAP_BYTES=6442450944
TYPESENSE_SNAPSHOT_CONTRACT=direct-mount-v1
TYPESENSE_SNAPSHOT_MIN_CAPACITY_BYTES=21474836480
TYPESENSE_SNAPSHOT_MIN_FREE_BYTES=8589934592
TYPESENSE_SNAPSHOT_GROWTH_RESERVE_BYTES=4294967296
TYPESENSE_READY_TIMEOUT_S=900
LOCK_TIMEOUT_S="${JOBSEEK_TYPESENSE_HOST_DEPLOY_LOCK_TIMEOUT_S:-120}"

typesense_tx_active=0
typesense_tx_candidate_config=""
typesense_tx_previous_config=""
typesense_tx_previous_config_exists=0
typesense_tx_previous_container_exists=0
typesense_tx_rollback_container_ready=0
typesense_tx_prior_timer_enabled=""
typesense_tx_prior_timer_active=""
typesense_tx_previous_pending=""
typesense_tx_previous_pending_exists=0
typesense_tx_previous_deployed=""
typesense_tx_previous_deployed_exists=0

prepare_host_deployment() {
  if [[ "$COMPONENT" == all || "$COMPONENT" == typesense ]]; then
    "$REPO_ROOT/deploy/typesense-host/check-memory-capacity.sh"
  fi
  install -d -o root -g root -m 0700 "$STATE_DIR" "$CREDENTIAL_DIR"
  exec 9>"$STATE_DIR/deploy.lock"
  if ! flock -w "$LOCK_TIMEOUT_S" 9; then
    echo "ERROR: another Typesense-host deployment holds the lock" >&2
    exit 1
  fi
}

validate_secret() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" || "$value" == *$'\n'* || "$value" == *$'\r'* || "$value" =~ [[:space:]] ]]; then
    echo "ERROR: $name must be a non-empty single token" >&2
    exit 1
  fi
}

atomic_install() {
  local destination="$1"
  local owner="$2"
  local group="$3"
  local mode="$4"
  local temporary
  temporary="$(mktemp "$CREDENTIAL_DIR/.install.XXXXXX")"
  chmod 0600 "$temporary"
  cat >"$temporary"
  chown "$owner:$group" "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$destination"
}

wait_for_typesense() {
  local deadline=$((SECONDS + TYPESENSE_READY_TIMEOUT_S))
  # Startup can legitimately return connection errors, 503s, or an empty body
  # for several minutes while Typesense replays its Raft log. Keep those
  # expected retries out of the deployment log; timeout handling below still
  # reports a terminal readiness failure.
  until curl --fail --silent --max-time 5 \
    http://127.0.0.1:8108/health 2>/dev/null |
    python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("ok") is True else 1)' 2>/dev/null
  do
    if (( SECONDS >= deadline )); then
      return 1
    fi
    sleep 2
  done
}

probe_typesense_bootstrap() {
  python3 - <<'PY'
import json
import os
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:8108/keys",
    headers={"X-TYPESENSE-API-KEY": os.environ["TYPESENSE_BOOTSTRAP_KEY"]},
)
with urllib.request.urlopen(request, timeout=10) as response:
    payload = json.load(response)
if not isinstance(payload.get("keys"), list):
    raise SystemExit("ERROR: Typesense bootstrap key failed its admin probe")
PY
}

validate_typesense_headroom() {
  python3 "$REPO_ROOT/scripts/verify-typesense-snapshot-mount.py" \
    --mount "$TYPESENSE_SNAPSHOT_DIR" \
    --live-data "$TYPESENSE_DATA_DIR" \
    --minimum-capacity "$TYPESENSE_SNAPSHOT_MIN_CAPACITY_BYTES" \
    --minimum-free "$TYPESENSE_SNAPSHOT_MIN_FREE_BYTES" \
    --growth-reserve "$TYPESENSE_SNAPSHOT_GROWTH_RESERVE_BYTES"
}

acquire_typesense_backup_locks() {
  test ! -L /run/jobseek-backup-deployment.lock
  exec 8>/run/jobseek-backup-deployment.lock
  chown root:root /run/jobseek-backup-deployment.lock
  chmod 0600 /run/jobseek-backup-deployment.lock
  if ! flock -w "$LOCK_TIMEOUT_S" 8; then
    echo "ERROR: another backup deployment holds the shared lock" >&2
    return 1
  fi
  test ! -L /run/jobseek-data-backup-typesense.lock
  exec 7>/run/jobseek-data-backup-typesense.lock
  chown root:root /run/jobseek-data-backup-typesense.lock
  chmod 0600 /run/jobseek-data-backup-typesense.lock
  if ! flock -w "$LOCK_TIMEOUT_S" 7; then
    echo "ERROR: a Typesense backup is active; host cutover did not quiesce it" >&2
    return 1
  fi
}

typesense_backup_timer_state() {
  local enabled active service_active
  enabled="$(systemctl is-enabled jobseek-typesense-backup.timer 2>/dev/null || true)"
  active="$(systemctl is-active jobseek-typesense-backup.timer 2>/dev/null || true)"
  service_active="$(systemctl is-active jobseek-typesense-backup.service 2>/dev/null || true)"
  [[ "$enabled" =~ ^(enabled|disabled)$ ]]
  [[ "$active" =~ ^(active|inactive)$ ]]
  [[ "$service_active" =~ ^(active|inactive|failed)$ ]]
  printf '%s %s %s\n' "$enabled" "$active" "$service_active"
}

quiesce_typesense_backup() {
  systemctl disable --now jobseek-typesense-backup.timer
  systemctl stop jobseek-typesense-backup.service
  [[ "$(typesense_backup_timer_state)" =~ ^disabled\ inactive\ (inactive|failed)$ ]]
}

restore_typesense_backup_timer() {
  local enabled=$1
  local active=$2
  local failed=0
  if ! systemctl reset-failed jobseek-typesense-backup.service; then
    failed=1
  fi
  if [[ "$enabled" == enabled ]]; then
    if ! systemctl enable jobseek-typesense-backup.timer; then
      failed=1
    fi
  fi
  if [[ "$active" == active ]]; then
    if ! systemctl start jobseek-typesense-backup.timer; then
      failed=1
    fi
  fi
  if [[ "$(systemctl is-enabled jobseek-typesense-backup.timer 2>/dev/null || true)" != \
      "$enabled" || \
    "$(systemctl is-active jobseek-typesense-backup.timer 2>/dev/null || true)" != \
      "$active" ]]; then
    failed=1
  fi
  if [[ "$failed" -ne 0 ]]; then
    echo "ERROR: could not restore the exact prior Typesense backup timer state" >&2
    if ! quiesce_typesense_backup; then
      echo "ERROR: Typesense backup timer rollback failed hard" >&2
    fi
    return 1
  fi
}

recover_typesense_container() {
  local source_name=$1
  if [[ "$source_name" != typesense ]]; then
    docker rename "$source_name" typesense || return 1
  fi
  if [[ "$(docker inspect typesense --format '{{.State.Running}}' 2>/dev/null || true)" != \
      true ]]; then
    docker start typesense >/dev/null || return 1
  fi
  wait_for_typesense
}

restore_optional_state_file() {
  local snapshot=$1
  local existed=$2
  local destination=$3
  if [[ "$existed" -eq 1 ]]; then
    if ! install -o root -g root -m 0644 "$snapshot" "$destination.tmp" ||
      ! sync -f "$destination.tmp" ||
      ! mv -f "$destination.tmp" "$destination" ||
      ! sync -f "$(dirname "$destination")"
    then
      return 1
    fi
  else
    if ! rm -f "$destination" "$destination.tmp" ||
      ! sync -f "$(dirname "$destination")"
    then
      return 1
    fi
  fi
}

cleanup_typesense_transaction_files() {
  if [[ -n "$typesense_tx_candidate_config" ]]; then
    rm -f "$typesense_tx_candidate_config"
  fi
  if [[ -n "$typesense_tx_previous_config" ]]; then
    rm -f "$typesense_tx_previous_config"
  fi
  if [[ -n "$typesense_tx_previous_pending" ]]; then
    rm -f "$typesense_tx_previous_pending"
  fi
  if [[ -n "$typesense_tx_previous_deployed" ]]; then
    rm -f "$typesense_tx_previous_deployed"
  fi
  typesense_tx_previous_config=""
  typesense_tx_candidate_config=""
  typesense_tx_previous_pending=""
  typesense_tx_previous_deployed=""
}

rollback_typesense_transaction() {
  local failed=0
  local container_failed=0
  local candidate_preserved=0
  local failed_candidate=typesense-transaction-failed-candidate
  local prior_container_named=0
  local prior_container_healthy=0
  if [[ "$typesense_tx_active" -ne 1 ]]; then
    return
  fi
  echo "ERROR: rolling back the staged Typesense host transaction" >&2
  if ! quiesce_typesense_backup; then
    failed=1
  fi

  if [[ "$typesense_tx_previous_config_exists" -eq 1 ]]; then
    if ! install -o root -g root -m 0600 \
      "$typesense_tx_previous_config" "$TYPESENSE_CONFIG"; then
      failed=1
    fi
  elif ! rm -f "$TYPESENSE_CONFIG"; then
    failed=1
  fi

  if [[ "$typesense_tx_rollback_container_ready" -eq 1 ]]; then
    if docker inspect typesense >/dev/null 2>&1; then
      if docker stop --time 60 typesense >/dev/null && \
        docker rename typesense "$failed_candidate"
      then
        candidate_preserved=1
      else
        container_failed=1
      fi
    fi
    if [[ "$container_failed" -eq 0 ]] && \
      docker rename typesense-credential-rollback typesense
    then
      prior_container_named=1
    else
      container_failed=1
    fi
    if [[ "$container_failed" -ne 0 ]]; then
      failed=1
    fi
  elif [[ "$typesense_tx_previous_container_exists" -eq 1 ]]; then
    if docker inspect typesense >/dev/null 2>&1; then
      prior_container_named=1
    else
      failed=1
    fi
  elif [[ "$typesense_tx_previous_container_exists" -ne 1 ]] && \
    docker inspect typesense >/dev/null 2>&1 && \
    ! docker rm --force typesense >/dev/null
  then
    failed=1
  fi

  if [[ "$typesense_tx_previous_container_exists" -eq 1 && \
    "$prior_container_named" -eq 1 ]] && recover_typesense_container typesense; then
    prior_container_healthy=1
  fi
  if [[ "$prior_container_healthy" -eq 1 && "$candidate_preserved" -eq 1 ]] && \
    ! docker rm "$failed_candidate" >/dev/null
  then
    echo "WARNING: failed Typesense candidate remains for operator cleanup" >&2
  fi

  if ! restore_optional_state_file \
      "$typesense_tx_previous_pending" \
      "$typesense_tx_previous_pending_exists" \
      "$STATE_DIR/backup-contract-pending"; then
    failed=1
  fi
  if ! restore_optional_state_file \
      "$typesense_tx_previous_deployed" \
      "$typesense_tx_previous_deployed_exists" \
      "$STATE_DIR/deployed-sha"; then
    failed=1
  fi

  if [[ "$typesense_tx_previous_container_exists" -eq 1 && \
    "$prior_container_healthy" -ne 1 ]]; then
    echo "ERROR: prior Typesense health could not be restored; backup timer remains disabled" >&2
    failed=1
  elif [[ "$typesense_tx_previous_container_exists" -ne 1 && \
    ( "$typesense_tx_prior_timer_enabled" != disabled || \
      "$typesense_tx_prior_timer_active" != inactive ) ]]; then
    echo "ERROR: no prior Typesense container exists; refusing to restore an active timer" >&2
    failed=1
  fi

  if [[ "$failed" -eq 0 && "$typesense_tx_previous_container_exists" -eq 1 ]]; then
    if ! restore_typesense_backup_timer \
      "$typesense_tx_prior_timer_enabled" "$typesense_tx_prior_timer_active"; then
      failed=1
    fi
  elif ! quiesce_typesense_backup; then
    failed=1
  fi
  cleanup_typesense_transaction_files
  typesense_tx_active=0
  if [[ "$failed" -ne 0 ]]; then
    echo "ERROR: Typesense host rollback failed hard; backup timer remains disabled" >&2
    return 1
  fi
}

typesense_host_exit() {
  local status=$?
  trap - EXIT
  if [[ "$typesense_tx_active" -eq 1 ]]; then
    if [[ "$status" -eq 0 ]]; then
      echo "ERROR: Typesense host transaction reached exit without an exact revision commit" >&2
      status=1
    fi
    if ! rollback_typesense_transaction; then
      status=1
    fi
  fi
  exit "$status"
}

mark_typesense_backup_contract_pending() {
  : "${JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA:?Typesense deploy SHA is required for staged rollout}"
  [[ "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]
  printf '%s\n' "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" >"$STATE_DIR/backup-contract-pending.tmp"
  chmod 0644 "$STATE_DIR/backup-contract-pending.tmp"
  sync -f "$STATE_DIR/backup-contract-pending.tmp"
  mv -f "$STATE_DIR/backup-contract-pending.tmp" "$STATE_DIR/backup-contract-pending"
  sync -f "$STATE_DIR"
}

write_typesense_deployed_revision() {
  : "${JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA:?Typesense deploy SHA is required}"
  [[ "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]
  printf '%s\n' "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" >"$STATE_DIR/deployed-sha.tmp"
  chmod 0644 "$STATE_DIR/deployed-sha.tmp"
  sync -f "$STATE_DIR/deployed-sha.tmp"
  mv -f "$STATE_DIR/deployed-sha.tmp" "$STATE_DIR/deployed-sha"
  sync -f "$STATE_DIR"
}

write_cloudflared_deployed_revision() {
  : "${JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA:?Cloudflared deploy SHA is required}"
  [[ "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]
  printf '%s\n' "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" \
    >"$STATE_DIR/cloudflared-deployed-sha.tmp"
  chmod 0644 "$STATE_DIR/cloudflared-deployed-sha.tmp"
  sync -f "$STATE_DIR/cloudflared-deployed-sha.tmp"
  mv -f \
    "$STATE_DIR/cloudflared-deployed-sha.tmp" \
    "$STATE_DIR/cloudflared-deployed-sha"
  sync -f "$STATE_DIR"
}

run_typesense_container() {
  docker run --detach \
    --name typesense \
    --restart unless-stopped \
    --network host \
    --ulimit "nofile=$TYPESENSE_NOFILE_LIMIT:$TYPESENSE_NOFILE_LIMIT" \
    --log-opt "max-size=$TYPESENSE_LOG_MAX_SIZE" \
    --log-opt "max-file=$TYPESENSE_LOG_MAX_FILES" \
    --memory "$TYPESENSE_MEMORY_LIMIT" \
    --memory-reservation "$TYPESENSE_MEMORY_RESERVATION" \
    --memory-swap "$TYPESENSE_MEMORY_SWAP" \
    --volume "$TYPESENSE_DATA_DIR:/data" \
    --volume "$TYPESENSE_SNAPSHOT_DIR:$TYPESENSE_SNAPSHOT_IN_CONTAINER" \
    --volume "$TYPESENSE_CONFIG:$TYPESENSE_CONFIG_IN_CONTAINER:ro" \
    --label jobseek.managed-by=deploy-typesense-host \
    --label "jobseek.typesense-snapshot-contract=$TYPESENSE_SNAPSHOT_CONTRACT" \
    "$TYPESENSE_IMAGE" \
    "--config=$TYPESENSE_CONFIG_IN_CONTAINER" >/dev/null
}

install_typesense() {
  : "${TYPESENSE_BOOTSTRAP_KEY:?TYPESENSE_BOOTSTRAP_KEY is required}"
  : "${JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA:?Typesense deploy SHA is required for staged rollout}"
  [[ "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]
  validate_secret TYPESENSE_BOOTSTRAP_KEY "$TYPESENSE_BOOTSTRAP_KEY"
  acquire_typesense_backup_locks
  test -d "$TYPESENSE_DATA_DIR"
  validate_typesense_headroom

  if [[ -e /var/lib/jobseek-backup/status/typesense.json ]]; then
    python3 - <<'PY'
import json
import time
from pathlib import Path

status = json.loads(
    Path("/var/lib/jobseek-backup/status/typesense.json").read_text(encoding="utf-8")
)
age = time.time() - int(status.get("last_success_unix") or 0)
if status.get("service") != "typesense" or age < 0 or age > 36 * 60 * 60:
    raise SystemExit("ERROR: Typesense last-success backup evidence is missing or stale")
PY
  else
    echo "ERROR: Typesense backup status is missing" >&2
    exit 1
  fi
  if systemctl is-active --quiet jobseek-typesense-backup.service; then
    echo "ERROR: Typesense backup won the service lock race; refusing to stop it" >&2
    exit 1
  fi

  local rollback_container=typesense-credential-rollback
  local failed_candidate=typesense-transaction-failed-candidate
  if docker inspect "$rollback_container" >/dev/null 2>&1 || \
    docker inspect "$failed_candidate" >/dev/null 2>&1
  then
    echo "ERROR: stale Typesense transaction container exists; refusing to overwrite it" >&2
    exit 1
  fi
  if docker inspect typesense >/dev/null 2>&1; then
    typesense_tx_previous_container_exists=1
  fi

  local backup_service_active
  read -r \
    typesense_tx_prior_timer_enabled \
    typesense_tx_prior_timer_active \
    backup_service_active <<<"$(typesense_backup_timer_state)"
  [[ "$backup_service_active" != active ]]

  test ! -L "$STATE_DIR/backup-contract-pending"
  test ! -L "$STATE_DIR/deployed-sha"
  test ! -L "$TYPESENSE_CONFIG"
  typesense_tx_previous_config="$(mktemp /run/jobseek-typesense-config.rollback.XXXXXX)"
  chmod 0600 "$typesense_tx_previous_config"
  if [[ -f "$TYPESENSE_CONFIG" ]]; then
    cp --preserve=mode,ownership "$TYPESENSE_CONFIG" "$typesense_tx_previous_config"
    typesense_tx_previous_config_exists=1
  fi
  typesense_tx_previous_pending="$(mktemp /run/jobseek-typesense-pending.rollback.XXXXXX)"
  typesense_tx_previous_deployed="$(mktemp /run/jobseek-typesense-deployed.rollback.XXXXXX)"
  if [[ -f "$STATE_DIR/backup-contract-pending" ]]; then
    cp -p "$STATE_DIR/backup-contract-pending" "$typesense_tx_previous_pending"
    typesense_tx_previous_pending_exists=1
  else
    rm -f "$typesense_tx_previous_pending"
  fi
  if [[ -f "$STATE_DIR/deployed-sha" ]]; then
    cp -p "$STATE_DIR/deployed-sha" "$typesense_tx_previous_deployed"
    typesense_tx_previous_deployed_exists=1
  else
    rm -f "$typesense_tx_previous_deployed"
  fi
  typesense_tx_active=1
  if ! quiesce_typesense_backup; then
    echo "ERROR: could not quiesce Typesense backup for the staged host contract" >&2
    return 1
  fi
  if ! docker pull "$TYPESENSE_IMAGE" >/dev/null; then
    echo "ERROR: could not pull the reviewed Typesense image" >&2
    return 1
  fi
  local candidate config_changed=1
  candidate="$(mktemp "$CREDENTIAL_DIR/.typesense-server.ini.XXXXXX")"
  typesense_tx_candidate_config="$candidate"
  chmod 0600 "$candidate"
  printf '%s\n' \
    '[server]' \
    'data-dir = /data' \
    "api-key = $TYPESENSE_BOOTSTRAP_KEY" \
    'api-port = 8108' \
    'listen-address = 0.0.0.0' >"$candidate"

  if [[ -f "$TYPESENSE_CONFIG" ]]; then
    if cmp --silent "$candidate" "$TYPESENSE_CONFIG"; then
      config_changed=0
    fi
  fi
  chown root:root "$candidate"
  chmod 0600 "$candidate"
  mv -f "$candidate" "$TYPESENSE_CONFIG"
  typesense_tx_candidate_config=""

  local container_conformant=0
  if docker inspect typesense >/dev/null 2>&1; then
    if docker inspect typesense |
      python3 -c '
import json
import sys

container = json.load(sys.stdin)[0]
(
    expected_image,
    expected_config_source,
    expected_config_destination,
    expected_snapshot_source,
    expected_snapshot_destination,
    expected_nofile,
    expected_log_size,
    expected_log_files,
    expected_memory,
    expected_memory_reservation,
    expected_memory_swap,
    expected_snapshot_contract,
) = sys.argv[1:]
cmd = container["Config"].get("Cmd") or []
mounts = container.get("Mounts") or []
ulimits = container["HostConfig"].get("Ulimits") or []
log_config = container["HostConfig"].get("LogConfig") or {}
labels = container["Config"].get("Labels") or {}
state = container.get("State") or {}
ok = (
    container["Config"].get("Image") == expected_image
    and container["HostConfig"].get("NetworkMode") == "host"
    and cmd == [f"--config={expected_config_destination}"]
    and any(
        mount.get("Source") == expected_config_source
        and mount.get("Destination") == expected_config_destination
        and mount.get("RW") is False
        for mount in mounts
    )
    and any(
        mount.get("Source") == expected_snapshot_source
        and mount.get("Destination") == expected_snapshot_destination
        and mount.get("RW") is True
        for mount in mounts
    )
    and any(
        limit.get("Name") == "nofile"
        and int(limit.get("Soft") or 0) == int(expected_nofile)
        and int(limit.get("Hard") or 0) == int(expected_nofile)
        for limit in ulimits
    )
    and log_config.get("Type") == "json-file"
    and (log_config.get("Config") or {}).get("max-size") == expected_log_size
    and (log_config.get("Config") or {}).get("max-file") == expected_log_files
    and int(container["HostConfig"].get("Memory") or 0) == int(expected_memory)
    and int(container["HostConfig"].get("MemoryReservation") or 0)
        == int(expected_memory_reservation)
    and int(container["HostConfig"].get("MemorySwap") or 0) == int(expected_memory_swap)
    and labels.get("jobseek.typesense-snapshot-contract") == expected_snapshot_contract
    and state.get("Running") is True
    and state.get("OOMKilled") is False
    and int(container.get("RestartCount") or 0) == 0
)
raise SystemExit(0 if ok else 1)
' \
        "$TYPESENSE_IMAGE" \
        "$TYPESENSE_CONFIG" \
        "$TYPESENSE_CONFIG_IN_CONTAINER" \
        "$TYPESENSE_SNAPSHOT_DIR" \
        "$TYPESENSE_SNAPSHOT_IN_CONTAINER" \
        "$TYPESENSE_NOFILE_LIMIT" \
        "$TYPESENSE_LOG_MAX_SIZE" \
        "$TYPESENSE_LOG_MAX_FILES" \
        "$TYPESENSE_MEMORY_LIMIT_BYTES" \
        "$TYPESENSE_MEMORY_RESERVATION_BYTES" \
        "$TYPESENSE_MEMORY_SWAP_BYTES" \
        "$TYPESENSE_SNAPSHOT_CONTRACT"
    then
      container_conformant=1
    fi
  fi

  if [[ "$config_changed" -eq 0 && "$container_conformant" -eq 1 ]] &&
    curl --fail --silent --max-time 5 http://127.0.0.1:8108/health >/dev/null
  then
    echo "Typesense host contract staged; backup timer remains disabled pending fresh snapshot"
    return
  fi

  if docker inspect typesense >/dev/null 2>&1; then
    if ! docker stop --time 60 typesense >/dev/null; then
      echo "ERROR: Typesense did not stop cleanly" >&2
      return 1
    fi
    if ! docker rename typesense "$rollback_container"; then
      echo "ERROR: could not preserve the prior Typesense container" >&2
      return 1
    fi
    typesense_tx_rollback_container_ready=1
  fi

  if ! run_typesense_container ||
    ! wait_for_typesense ||
    ! probe_typesense_bootstrap
  then
    echo "ERROR: managed Typesense start failed" >&2
    return 1
  fi

  echo "Installed protected Typesense bootstrap-key delivery"
}

wait_for_tunnel() {
  local deadline=$((SECONDS + 120))
  until systemctl is-active --quiet cloudflared.service &&
    curl --fail --silent --show-error --max-time 10 \
      https://typesense.colophon-group.org/health |
      python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("ok") is True else 1)'
  do
    if (( SECONDS >= deadline )); then
      return 1
    fi
    sleep 2
  done
}

install_cloudflared() {
  : "${CLOUDFLARE_TUNNEL_TOKEN:?CLOUDFLARE_TUNNEL_TOKEN is required}"
  validate_secret CLOUDFLARE_TUNNEL_TOKEN "$CLOUDFLARE_TUNNEL_TOKEN"

  if ! getent group cloudflared >/dev/null; then
    groupadd --system cloudflared
  fi
  if ! getent passwd cloudflared >/dev/null; then
    useradd --system \
      --gid cloudflared \
      --home-dir /nonexistent \
      --shell /usr/sbin/nologin \
      cloudflared
  fi

  systemd-analyze verify "$REPO_ROOT/deploy/systemd/cloudflared.service"

  local previous_unit previous_token previous_revision
  local unit_existed=0 token_existed=0 revision_existed=0
  local token_changed=1 unit_changed=1
  previous_unit="$(mktemp /run/cloudflared.service.rollback.XXXXXX)"
  previous_token="$(mktemp /run/cloudflared-token.rollback.XXXXXX)"
  previous_revision="$(mktemp /run/cloudflared-deployed.rollback.XXXXXX)"
  chmod 0600 "$previous_unit" "$previous_token" "$previous_revision"
  if [[ -f "$CLOUDFLARED_UNIT" ]]; then
    cp --preserve=mode,ownership "$CLOUDFLARED_UNIT" "$previous_unit"
    unit_existed=1
    if cmp --silent \
      "$REPO_ROOT/deploy/systemd/cloudflared.service" \
      "$CLOUDFLARED_UNIT"
    then
      unit_changed=0
    fi
  fi
  if [[ -f "$CLOUDFLARED_TOKEN_FILE" ]]; then
    cp --preserve=mode,ownership "$CLOUDFLARED_TOKEN_FILE" "$previous_token"
    token_existed=1
    if [[ "$(cat "$CLOUDFLARED_TOKEN_FILE")" == "$CLOUDFLARE_TUNNEL_TOKEN" ]]; then
      token_changed=0
    fi
  fi
  test ! -L "$STATE_DIR/cloudflared-deployed-sha"
  if [[ -f "$STATE_DIR/cloudflared-deployed-sha" ]]; then
    cp -p "$STATE_DIR/cloudflared-deployed-sha" "$previous_revision"
    revision_existed=1
  else
    rm -f "$previous_revision"
  fi

  rollback_cloudflared() {
    local failed=0
    if ! systemctl stop cloudflared.service >/dev/null 2>&1; then
      failed=1
    fi
    if [[ "$unit_existed" -eq 1 ]]; then
      if ! install -o root -g root -m 0644 "$previous_unit" "$CLOUDFLARED_UNIT"; then
        failed=1
      fi
    elif ! rm -f "$CLOUDFLARED_UNIT"; then
      failed=1
    fi
    if [[ "$token_existed" -eq 1 ]]; then
      if ! install -o root -g root -m 0600 \
        "$previous_token" "$CLOUDFLARED_TOKEN_FILE"; then
        failed=1
      fi
    elif ! rm -f "$CLOUDFLARED_TOKEN_FILE"; then
      failed=1
    fi
    if ! restore_optional_state_file \
      "$previous_revision" "$revision_existed" \
      "$STATE_DIR/cloudflared-deployed-sha"; then
      failed=1
    fi
    if ! systemctl daemon-reload >/dev/null 2>&1; then
      failed=1
    fi
    if [[ "$unit_existed" -eq 1 ]]; then
      if ! systemctl restart cloudflared.service >/dev/null 2>&1 ||
        ! wait_for_tunnel; then
        failed=1
      fi
    fi
    if [[ "$failed" -ne 0 ]]; then
      return 1
    fi
  }

  if ! printf '%s\n' "$CLOUDFLARE_TUNNEL_TOKEN" |
      atomic_install "$CLOUDFLARED_TOKEN_FILE" root root 0600 ||
    ! install -o root -g root -m 0644 \
      "$REPO_ROOT/deploy/systemd/cloudflared.service" \
      "$CLOUDFLARED_UNIT" ||
    ! systemctl daemon-reload
  then
    echo "ERROR: could not stage protected Cloudflare Tunnel delivery" >&2
    if ! rollback_cloudflared; then
      echo "ERROR: Cloudflare Tunnel rollback failed hard" >&2
    fi
    rm -f "$previous_unit" "$previous_token" "$previous_revision"
    exit 1
  fi

  local unit_active=0
  if systemctl is-active --quiet cloudflared.service; then
    unit_active=1
  fi
  if [[ "$token_changed" -eq 0 && "$unit_changed" -eq 0 && "$unit_active" -eq 1 ]] &&
    systemctl show cloudflared.service -p ExecStart --value |
      grep -Fq -- "--token-file /run/credentials/cloudflared.service/cloudflare-tunnel-token"
  then
    if ! write_cloudflared_deployed_revision; then
      echo "ERROR: could not commit the cloudflared deployed revision" >&2
      if ! rollback_cloudflared; then
        echo "ERROR: Cloudflare Tunnel rollback failed hard" >&2
      fi
      rm -f "$previous_unit" "$previous_token" "$previous_revision"
      exit 1
    fi
    rm -f "$previous_unit" "$previous_token" "$previous_revision"
    echo "Cloudflare Tunnel credential delivery already conforms; restart skipped"
    return
  fi

  if ! systemctl enable cloudflared.service >/dev/null ||
    ! systemctl restart cloudflared.service ||
    ! wait_for_tunnel ||
    ! write_cloudflared_deployed_revision
  then
    echo "ERROR: protected Cloudflare Tunnel start failed; restoring prior unit" >&2
    if ! rollback_cloudflared; then
      echo "ERROR: Cloudflare Tunnel rollback failed hard" >&2
    fi
    rm -f "$previous_unit" "$previous_token" "$previous_revision"
    exit 1
  fi
  rm -f "$previous_unit" "$previous_token" "$previous_revision"
  echo "Installed protected Cloudflare Tunnel token-file delivery"
}

trap typesense_host_exit EXIT
prepare_host_deployment

case "$COMPONENT" in
  all)
    install_typesense
    install_cloudflared
    ;;
  typesense)
    install_typesense
    ;;
  cloudflared)
    install_cloudflared
    ;;
esac

install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/verify-typesense-host-credentials.py" \
  /usr/local/sbin/jobseek-verify-typesense-host-credentials
install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/verify-typesense-snapshot-mount.py" \
  /usr/local/sbin/jobseek-verify-typesense-snapshot-mount
/usr/local/sbin/jobseek-verify-typesense-host-credentials --component "$COMPONENT"
if [[ "$typesense_tx_active" -eq 1 ]]; then
  mark_typesense_backup_contract_pending
  write_typesense_deployed_revision
  test "$(cat "$STATE_DIR/backup-contract-pending")" = \
    "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA"
  test "$(cat "$STATE_DIR/deployed-sha")" = "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA"
  typesense_tx_active=0
  cleanup_typesense_transaction_files
  if [[ "$typesense_tx_rollback_container_ready" -eq 1 ]] && \
    ! docker rm typesense-credential-rollback >/dev/null
  then
    echo "WARNING: committed Typesense rollback container remains for operator cleanup" >&2
  fi
fi
echo "Installed Typesense host surface; component=$COMPONENT"

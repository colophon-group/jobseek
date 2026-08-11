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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR=/var/lib/jobseek-typesense-host
CREDENTIAL_DIR=/etc/jobseek-typesense
TYPESENSE_CONFIG="$CREDENTIAL_DIR/typesense-server.ini"
CLOUDFLARED_TOKEN_FILE="$CREDENTIAL_DIR/cloudflare-tunnel-token"
CLOUDFLARED_UNIT=/etc/systemd/system/cloudflared.service
TYPESENSE_IMAGE=typesense/typesense:27.1
TYPESENSE_DATA_DIR=/mnt/typesense-data
TYPESENSE_SNAPSHOT_DIR=/mnt/jobseek-typesense-backup
TYPESENSE_CONFIG_IN_CONTAINER=/run/secrets/typesense-server.ini
TYPESENSE_SNAPSHOT_IN_CONTAINER=/jobseek-snapshots
TYPESENSE_NOFILE_LIMIT=65536
TYPESENSE_LOG_MAX_SIZE=50m
TYPESENSE_LOG_MAX_FILES=3
TYPESENSE_MEMORY_LIMIT=3g
TYPESENSE_MEMORY_RESERVATION=2560m
TYPESENSE_MEMORY_LIMIT_BYTES=3221225472
TYPESENSE_MEMORY_RESERVATION_BYTES=2684354560
TYPESENSE_SNAPSHOT_MIN_FREE_BYTES=8589934592
TYPESENSE_READY_TIMEOUT_S=900
LOCK_TIMEOUT_S="${JOBSEEK_TYPESENSE_HOST_DEPLOY_LOCK_TIMEOUT_S:-120}"

install -d -o root -g root -m 0700 "$STATE_DIR" "$CREDENTIAL_DIR"
exec 9>"$STATE_DIR/deploy.lock"
if ! flock -w "$LOCK_TIMEOUT_S" 9; then
  echo "ERROR: another Typesense-host deployment holds the lock" >&2
  exit 1
fi

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
  local root_device data_device snapshot_device snapshot_free
  if ! findmnt --mountpoint --noheadings --output TARGET "$TYPESENSE_SNAPSHOT_DIR" |
    grep -Fxq "$TYPESENSE_SNAPSHOT_DIR"
  then
    echo "ERROR: Typesense snapshot staging must be a dedicated mounted filesystem" >&2
    exit 1
  fi
  test ! -L "$TYPESENSE_SNAPSHOT_DIR"
  root_device="$(stat -c '%d' /)"
  data_device="$(stat -c '%d' "$TYPESENSE_DATA_DIR")"
  snapshot_device="$(stat -c '%d' "$TYPESENSE_SNAPSHOT_DIR")"
  if [[ "$snapshot_device" == "$root_device" || "$snapshot_device" == "$data_device" ]]; then
    echo "ERROR: Typesense snapshot staging must be isolated from root and live data" >&2
    exit 1
  fi
  snapshot_free="$(df --output=avail -B1 "$TYPESENSE_SNAPSHOT_DIR" | tail -n 1 | tr -d ' ')"
  if (( snapshot_free < TYPESENSE_SNAPSHOT_MIN_FREE_BYTES )); then
    echo "ERROR: Typesense snapshot staging has less than 8 GiB free" >&2
    exit 1
  fi
  chown root:root "$TYPESENSE_SNAPSHOT_DIR"
  chmod 0700 "$TYPESENSE_SNAPSHOT_DIR"
}

run_typesense_container() {
  docker run --detach \
    --name typesense \
    --restart unless-stopped \
    --network host \
    --ulimit "nofile=$TYPESENSE_NOFILE_LIMIT:$TYPESENSE_NOFILE_LIMIT" \
    --memory "$TYPESENSE_MEMORY_LIMIT" \
    --memory-reservation "$TYPESENSE_MEMORY_RESERVATION" \
    --memory-swap "$TYPESENSE_MEMORY_LIMIT" \
    --log-opt "max-size=$TYPESENSE_LOG_MAX_SIZE" \
    --log-opt "max-file=$TYPESENSE_LOG_MAX_FILES" \
    --volume "$TYPESENSE_DATA_DIR:/data" \
    --volume "$TYPESENSE_SNAPSHOT_DIR:$TYPESENSE_SNAPSHOT_IN_CONTAINER" \
    --volume "$TYPESENSE_CONFIG:$TYPESENSE_CONFIG_IN_CONTAINER:ro" \
    --label jobseek.managed-by=deploy-typesense-host \
    "$TYPESENSE_IMAGE" \
    "--config=$TYPESENSE_CONFIG_IN_CONTAINER" >/dev/null
}

install_typesense() {
  : "${TYPESENSE_BOOTSTRAP_KEY:?TYPESENSE_BOOTSTRAP_KEY is required}"
  validate_secret TYPESENSE_BOOTSTRAP_KEY "$TYPESENSE_BOOTSTRAP_KEY"
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
if status.get("success") is not True or age < 0 or age > 36 * 60 * 60:
    raise SystemExit("ERROR: Typesense backup evidence is failed or stale")
PY
  else
    echo "ERROR: Typesense backup status is missing" >&2
    exit 1
  fi
  if systemctl is-active --quiet jobseek-typesense-backup.service; then
    echo "ERROR: Typesense backup is active; refusing to restart the service" >&2
    exit 1
  fi

  docker pull "$TYPESENSE_IMAGE" >/dev/null

  local rollback_container=typesense-credential-rollback
  if docker inspect "$rollback_container" >/dev/null 2>&1; then
    echo "ERROR: stale Typesense rollback container exists; refusing to overwrite it" >&2
    exit 1
  fi

  local candidate previous_config previous_config_exists=0 config_changed=1
  candidate="$(mktemp "$CREDENTIAL_DIR/.typesense-server.ini.XXXXXX")"
  previous_config="$(mktemp /run/jobseek-typesense-config.rollback.XXXXXX)"
  chmod 0600 "$candidate" "$previous_config"
  printf '%s\n' \
    '[server]' \
    'data-dir = /data' \
    "api-key = $TYPESENSE_BOOTSTRAP_KEY" \
    'api-port = 8108' \
    'listen-address = 0.0.0.0' >"$candidate"

  if [[ -f "$TYPESENSE_CONFIG" ]]; then
    cp --preserve=mode,ownership "$TYPESENSE_CONFIG" "$previous_config"
    previous_config_exists=1
    if cmp --silent "$candidate" "$TYPESENSE_CONFIG"; then
      config_changed=0
    fi
  fi
  chown root:root "$candidate"
  chmod 0600 "$candidate"
  mv -f "$candidate" "$TYPESENSE_CONFIG"

  local container_conformant=0
  if docker inspect typesense >/dev/null 2>&1; then
    if docker inspect typesense |
      python3 -c '
import json
import sys

container = json.load(sys.stdin)[0]
(
    expected_config_source,
    expected_config_destination,
    expected_snapshot_source,
    expected_snapshot_destination,
    expected_nofile,
    expected_log_size,
    expected_log_files,
    expected_memory,
    expected_memory_reservation,
) = sys.argv[1:]
cmd = container["Config"].get("Cmd") or []
mounts = container.get("Mounts") or []
ulimits = container["HostConfig"].get("Ulimits") or []
log_config = container["HostConfig"].get("LogConfig") or {}
ok = (
    container["Config"].get("Image") == "typesense/typesense:27.1"
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
    and int(container["HostConfig"].get("MemorySwap") or 0) == int(expected_memory)
)
raise SystemExit(0 if ok else 1)
' \
        "$TYPESENSE_CONFIG" \
        "$TYPESENSE_CONFIG_IN_CONTAINER" \
        "$TYPESENSE_SNAPSHOT_DIR" \
        "$TYPESENSE_SNAPSHOT_IN_CONTAINER" \
        "$TYPESENSE_NOFILE_LIMIT" \
        "$TYPESENSE_LOG_MAX_SIZE" \
        "$TYPESENSE_LOG_MAX_FILES" \
        "$TYPESENSE_MEMORY_LIMIT_BYTES" \
        "$TYPESENSE_MEMORY_RESERVATION_BYTES"
    then
      container_conformant=1
    fi
  fi

  if [[ "$config_changed" -eq 0 && "$container_conformant" -eq 1 ]] &&
    curl --fail --silent --max-time 5 http://127.0.0.1:8108/health >/dev/null
  then
    rm -f "$previous_config"
    echo "Typesense credential delivery already conforms; restart skipped"
    return
  fi

  local previous_container_exists=0
  if docker inspect typesense >/dev/null 2>&1; then
    previous_container_exists=1
    if ! docker stop --time 60 typesense >/dev/null; then
      echo "ERROR: Typesense did not stop cleanly; restoring the prior config" >&2
      if [[ "$previous_config_exists" -eq 1 ]]; then
        install -o root -g root -m 0600 "$previous_config" "$TYPESENSE_CONFIG"
      else
        rm -f "$TYPESENSE_CONFIG"
      fi
      rm -f "$previous_config"
      exit 1
    fi
    if ! docker rename typesense "$rollback_container"; then
      echo "ERROR: could not preserve the prior Typesense container" >&2
      if [[ "$previous_config_exists" -eq 1 ]]; then
        install -o root -g root -m 0600 "$previous_config" "$TYPESENSE_CONFIG"
      else
        rm -f "$TYPESENSE_CONFIG"
      fi
      docker start typesense >/dev/null 2>&1 || true
      rm -f "$previous_config"
      exit 1
    fi
  fi

  if ! run_typesense_container ||
    ! wait_for_typesense ||
    ! probe_typesense_bootstrap
  then
    echo "ERROR: managed Typesense start failed; restoring the prior service" >&2
    docker rm --force typesense >/dev/null 2>&1 || true
    if [[ "$previous_config_exists" -eq 1 ]]; then
      install -o root -g root -m 0600 "$previous_config" "$TYPESENSE_CONFIG"
    else
      rm -f "$TYPESENSE_CONFIG"
    fi
    if [[ "$previous_container_exists" -eq 1 ]]; then
      docker rename "$rollback_container" typesense >/dev/null 2>&1 || true
      docker start typesense >/dev/null 2>&1 || true
      wait_for_typesense || true
    fi
    rm -f "$previous_config"
    exit 1
  fi
  if [[ "$previous_container_exists" -eq 1 ]]; then
    docker rm "$rollback_container" >/dev/null
  fi
  rm -f "$previous_config"

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

  local previous_unit previous_token
  local unit_existed=0 token_existed=0 token_changed=1 unit_changed=1
  previous_unit="$(mktemp /run/cloudflared.service.rollback.XXXXXX)"
  previous_token="$(mktemp /run/cloudflared-token.rollback.XXXXXX)"
  chmod 0600 "$previous_unit" "$previous_token"
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

  rollback_cloudflared() {
    systemctl stop cloudflared.service >/dev/null 2>&1 || true
    if [[ "$unit_existed" -eq 1 ]]; then
      install -o root -g root -m 0644 "$previous_unit" "$CLOUDFLARED_UNIT"
    else
      rm -f "$CLOUDFLARED_UNIT"
    fi
    if [[ "$token_existed" -eq 1 ]]; then
      install -o root -g root -m 0600 \
        "$previous_token" "$CLOUDFLARED_TOKEN_FILE"
    else
      rm -f "$CLOUDFLARED_TOKEN_FILE"
    fi
    systemctl daemon-reload >/dev/null 2>&1 || true
    if [[ "$unit_existed" -eq 1 ]]; then
      systemctl restart cloudflared.service >/dev/null 2>&1 || true
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
    rollback_cloudflared
    rm -f "$previous_unit" "$previous_token"
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
    rm -f "$previous_unit" "$previous_token"
    echo "Cloudflare Tunnel credential delivery already conforms; restart skipped"
    return
  fi

  if ! systemctl enable cloudflared.service >/dev/null ||
    ! systemctl restart cloudflared.service ||
    ! wait_for_tunnel
  then
    echo "ERROR: protected Cloudflare Tunnel start failed; restoring prior unit" >&2
    rollback_cloudflared
    rm -f "$previous_unit" "$previous_token"
    exit 1
  fi
  rm -f "$previous_unit" "$previous_token"
  echo "Installed protected Cloudflare Tunnel token-file delivery"
}

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
if [[ -n "${JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA:-}" ]]; then
  [[ "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]
  printf '%s\n' "$JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA" >"$STATE_DIR/deployed-sha.tmp"
  chmod 0644 "$STATE_DIR/deployed-sha.tmp"
  mv -f "$STATE_DIR/deployed-sha.tmp" "$STATE_DIR/deployed-sha"
fi

/usr/local/sbin/jobseek-verify-typesense-host-credentials --component "$COMPONENT"
echo "Installed Typesense host surface; component=$COMPONENT"

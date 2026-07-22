#!/usr/bin/env bash
# Install or update the pinned, host-level Jobseek observability surface.
set -euo pipefail

ALLOY_VERSION="1.18.0"
ALLOY_IMAGE="grafana/alloy:v${ALLOY_VERSION}@sha256:491b0578c04983fd54fe99b587b6fab4404dc46d0dc16677bd6b00cc1140b308"
ROLE="${1:-}"
DEPLOY_SHA="${JOBSEEK_OBSERVABILITY_DEPLOY_SHA:-}"
LOCK_TIMEOUT_S="${JOBSEEK_OBSERVABILITY_DEPLOY_LOCK_TIMEOUT_S:-120}"

case "$ROLE" in
  crawler) HOST_INSTANCE="jobseek-crawler-browser" ;;
  postgresql) HOST_INSTANCE="jobseek-postgresql" ;;
  typesense) HOST_INSTANCE="jobseek-typesense" ;;
  *)
    echo "Usage: $0 <crawler|postgresql|typesense>" >&2
    exit 2
    ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_ROOT=/etc/jobseek-observability
STATE_ROOT=/var/lib/jobseek-observability
ROLLBACK_ROOT="${STATE_ROOT}/rollback"
BINARY=/usr/local/bin/jobseek-alloy
SAMPLER=/usr/local/sbin/jobseek-host-observability
UNITS=(
  jobseek-alloy.service
  jobseek-host-observability.service
  jobseek-host-observability.timer
)
REQUIRED_ENV=(
  GRAFANA_PROM_URL
  GRAFANA_PROM_USERNAME
  GRAFANA_PROM_PASSWORD
  GRAFANA_LOKI_URL
  GRAFANA_LOKI_USERNAME
  GRAFANA_LOKI_PASSWORD
)
ROLLBACK_ARMED=0
ROLLBACK_PATH=""

log() {
  printf '==> %s\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ "$(id -u)" -eq 0 ]] || fail "must run as root"
  command -v docker >/dev/null || fail "docker is required to obtain the pinned Alloy binary"
  command -v systemctl >/dev/null || fail "systemd is required"
  local name value
  for name in "${REQUIRED_ENV[@]}"; do
    value="${!name:-}"
    [[ -n "$value" ]] || fail "missing required environment variable: ${name}"
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || fail "newline in ${name}"
  done
  if [[ -n "$DEPLOY_SHA" ]]; then
    [[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "invalid deployment SHA"
  fi
}

write_env_line() {
  local name="$1" value="$2"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s="%s"\n' "$name" "$value"
}

write_runtime_env() {
  umask 077
  local alloy_tmp host_tmp
  alloy_tmp="$(mktemp "${CONFIG_ROOT}/.alloy.env.XXXXXX")"
  host_tmp="$(mktemp "${CONFIG_ROOT}/.host.env.XXXXXX")"
  {
    write_env_line JOBSEEK_HOST_ROLE "$ROLE"
    write_env_line JOBSEEK_HOST_INSTANCE "$HOST_INSTANCE"
    write_env_line PROM_URL "$GRAFANA_PROM_URL"
    write_env_line PROM_USERNAME "$GRAFANA_PROM_USERNAME"
    write_env_line PROM_PASSWORD "$GRAFANA_PROM_PASSWORD"
    write_env_line LOKI_URL "$GRAFANA_LOKI_URL"
    write_env_line LOKI_USERNAME "$GRAFANA_LOKI_USERNAME"
    write_env_line LOKI_PASSWORD "$GRAFANA_LOKI_PASSWORD"
  } >"$alloy_tmp"
  write_env_line JOBSEEK_HOST_ROLE "$ROLE" >"$host_tmp"
  chmod 0600 "$alloy_tmp" "$host_tmp"
  mv "$alloy_tmp" "${CONFIG_ROOT}/alloy.env"
  mv "$host_tmp" "${CONFIG_ROOT}/host.env"
}

extract_pinned_alloy() {
  local container entrypoint staged
  log "pulling pinned Alloy ${ALLOY_VERSION}"
  docker pull "$ALLOY_IMAGE" >/dev/null
  entrypoint="$(docker image inspect --format '{{index .Config.Entrypoint 0}}' "$ALLOY_IMAGE")"
  [[ "$entrypoint" == /* && "${entrypoint##*/}" == alloy ]] ||
    fail "pinned Alloy image has an unexpected entrypoint"
  container="$(docker create "$ALLOY_IMAGE")"
  staged="$(mktemp /usr/local/bin/.jobseek-alloy.XXXXXX)"
  cleanup_extract() {
    docker rm "$container" >/dev/null 2>&1 || true
    if [[ -e "$staged" ]]; then
      rm -f "$staged"
    fi
  }
  trap cleanup_extract RETURN
  docker cp "${container}:${entrypoint}" "$staged"
  chmod 0755 "$staged"
  "$staged" --version | grep -F "${ALLOY_VERSION}" >/dev/null ||
    fail "extracted Alloy binary does not report ${ALLOY_VERSION}"
  mv "$staged" "$BINARY"
  docker rm "$container" >/dev/null
  trap - RETURN
}

snapshot_previous() {
  local stamp path unit
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  path="${ROLLBACK_ROOT}/${stamp}"
  install -d -m 0700 "$path"
  for source in \
    "$BINARY" \
    "$SAMPLER" \
    "${CONFIG_ROOT}/alloy-host.alloy" \
    "${CONFIG_ROOT}/alloy.env" \
    "${CONFIG_ROOT}/host.env"; do
    if [[ -e "$source" ]]; then
      cp --archive "$source" "$path/"
    fi
  done
  for unit in "${UNITS[@]}"; do
    if [[ -e "/etc/systemd/system/${unit}" ]]; then
      cp --archive "/etc/systemd/system/${unit}" "$path/"
    fi
  done
  printf '%s\n' "$path"
}

restore_previous() {
  local rollback="$1" unit
  set +e
  log "deployment failed; restoring previous observability surface"
  systemctl stop jobseek-alloy.service jobseek-host-observability.timer >/dev/null 2>&1
  if [[ -f "${rollback}/jobseek-alloy" ]]; then
    install -o root -g root -m 0755 "${rollback}/jobseek-alloy" "$BINARY"
  fi
  if [[ -f "${rollback}/jobseek-host-observability" ]]; then
    install -o root -g root -m 0755 "${rollback}/jobseek-host-observability" "$SAMPLER"
  fi
  for name in alloy-host.alloy alloy.env host.env; do
    if [[ -f "${rollback}/${name}" ]]; then
      install -o root -g root -m "$([[ "$name" == *.env ]] && echo 0600 || echo 0644)" \
        "${rollback}/${name}" "${CONFIG_ROOT}/${name}"
    fi
  done
  for unit in "${UNITS[@]}"; do
    if [[ -f "${rollback}/${unit}" ]]; then
      install -o root -g root -m 0644 "${rollback}/${unit}" "/etc/systemd/system/${unit}"
    elif [[ -f "/etc/systemd/system/${unit}" ]]; then
      rm -f "/etc/systemd/system/${unit}"
    fi
  done
  systemctl daemon-reload
  if [[ -f "${rollback}/jobseek-alloy.service" ]]; then
    systemctl enable --now jobseek-alloy.service
    systemctl enable --now jobseek-host-observability.timer
  fi
}

rollback_on_exit() {
  local deploy_status=$?
  trap - EXIT
  if [[ "$ROLLBACK_ARMED" == "1" && "$deploy_status" -ne 0 ]]; then
    restore_previous "$ROLLBACK_PATH"
  fi
  exit "$deploy_status"
}

install_surface() {
  local unit

  extract_pinned_alloy
  install -o root -g root -m 0755 \
    "${REPO_ROOT}/scripts/jobseek-host-observability.py" "$SAMPLER"
  install -o root -g root -m 0644 \
    "${REPO_ROOT}/deploy/observability/alloy-host.alloy" \
    "${CONFIG_ROOT}/alloy-host.alloy"
  write_runtime_env

  set -a
  # shellcheck disable=SC1091
  source "${CONFIG_ROOT}/alloy.env"
  set +a
  "$BINARY" validate "${CONFIG_ROOT}/alloy-host.alloy"

  for unit in "${UNITS[@]}"; do
    install -o root -g root -m 0644 \
      "${REPO_ROOT}/deploy/systemd/${unit}" "/etc/systemd/system/${unit}"
  done
  systemctl daemon-reload
  systemd-analyze verify "${UNITS[@]/#//etc/systemd/system/}"

  systemctl start jobseek-host-observability.service
  systemctl enable --now jobseek-host-observability.timer
  systemctl enable jobseek-alloy.service
  systemctl restart jobseek-alloy.service
  systemctl is-active --quiet jobseek-alloy.service
  systemctl is-active --quiet jobseek-host-observability.timer

  local ready=0
  for _ in {1..20}; do
    if /usr/bin/curl --silent --show-error --fail \
      http://127.0.0.1:12345/-/ready >/dev/null; then
      ready=1
      break
    fi
    sleep 1
  done
  [[ "$ready" -eq 1 ]] || fail "Alloy did not become ready on loopback"

  if [[ -n "$DEPLOY_SHA" ]]; then
    printf '%s\n' "$DEPLOY_SHA" >"${STATE_ROOT}/deployed-sha.tmp"
    chmod 0644 "${STATE_ROOT}/deployed-sha.tmp"
    mv "${STATE_ROOT}/deployed-sha.tmp" "${STATE_ROOT}/deployed-sha"
  fi
}

main() {
  require_root
  exec 9>/run/jobseek-observability-deploy.lock
  flock -w "$LOCK_TIMEOUT_S" 9 || fail "another observability deploy is active"

  if ! id -u jobseek-alloy >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin jobseek-alloy
  fi
  usermod -a -G systemd-journal jobseek-alloy
  install -d -o root -g root -m 0700 "$CONFIG_ROOT" "$STATE_ROOT" "$ROLLBACK_ROOT"
  install -d -o root -g root -m 0755 "${STATE_ROOT}/textfile"
  install -d -o root -g root -m 0700 "${STATE_ROOT}/state"

  local rollback
  rollback="$(snapshot_previous)"
  ROLLBACK_PATH="$rollback"
  ROLLBACK_ARMED=1
  trap rollback_on_exit EXIT
  install_surface
  ROLLBACK_ARMED=0
  trap - EXIT
  log "installed host observability role=${ROLE} sha=${DEPLOY_SHA:-manual}"
}

main "$@"

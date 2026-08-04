#!/usr/bin/env bash
# Run one bounded data-only ATS inventory refresh and queue pass.
set -euo pipefail
umask 077

STATE_ROOT=/var/lib/jobseek-ats-inventory
CONFIG=/etc/jobseek-ats-inventory/config.env
WRITE_DISABLED=/etc/jobseek-ats-inventory/writes-disabled
DEPLOY_ENV=/home/deploy/.env
CONTAINER=jobseek-ats-inventory
TOKEN_FILE=""
RUN_LOG=""

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  [[ -z "$TOKEN_FILE" ]] || rm -f -- "$TOKEN_FILE"
  [[ -z "$RUN_LOG" ]] || rm -f -- "$RUN_LOG"
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

for command in awk date docker flock grep mktemp openssl python3 sed sha256sum tee timeout tr; do
  command -v "$command" >/dev/null || {
    echo "ERROR: required command ${command} is unavailable" >&2
    exit 1
  }
done
[[ -r "$DEPLOY_ENV" ]] || { echo "ERROR: crawler deployment environment is unavailable" >&2; exit 1; }
[[ -r "$CONFIG" ]] || { echo "ERROR: ATS inventory configuration is unavailable" >&2; exit 1; }
[[ -n "${CREDENTIALS_DIRECTORY:-}" && -d "$CREDENTIALS_DIRECTORY" ]] || {
  echo "ERROR: GitHub App systemd credentials are unavailable" >&2
  exit 1
}
exec 9>/run/lock/jobseek-ats-inventory-host.lock
flock -n 9 || {
  echo "ERROR: another ATS inventory host run is active" >&2
  exit 75
}

read_exact_config() {
  local key="$1"
  mapfile -t matches < <(sed -n "s/^${key}=//p" "$CONFIG")
  [[ ${#matches[@]} -eq 1 && -n "${matches[0]}" ]] || {
    echo "ERROR: ${key} must appear exactly once in the ATS inventory config" >&2
    exit 1
  }
  printf '%s' "${matches[0]}"
}

requested_mode="$(read_exact_config ATS_INVENTORY_MODE)"
rollout_cap="$(read_exact_config ATS_INVENTORY_ROLLOUT_CAP)"
case "$requested_mode" in report|dry-run|refill) ;; *) echo "ERROR: invalid ATS inventory mode" >&2; exit 1 ;; esac
case "$rollout_cap" in 1|5|25) ;; *) echo "ERROR: invalid ATS inventory rollout cap" >&2; exit 1 ;; esac
effective_mode="$requested_mode"
if [[ -e "$WRITE_DISABLED" ]]; then
  effective_mode=report
  printf '%s\n' \
    '{"event":"ats_inventory.writes_disabled","effective_mode":"report"}'
fi

revision="$(tr -d '\n' <"$STATE_ROOT/deployed-sha")"
wrapper_sha="$(tr -d '\n' <"$STATE_ROOT/wrapper-sha256")"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo "ERROR: invalid deployment revision" >&2; exit 1; }
[[ "$wrapper_sha" =~ ^[0-9a-f]{64}$ ]] || { echo "ERROR: invalid wrapper digest" >&2; exit 1; }
[[ "$(sha256sum /usr/local/sbin/jobseek-ats-inventory | awk '{print $1}')" == "$wrapper_sha" ]] || {
  echo "ERROR: installed ATS inventory wrapper digest drifted" >&2
  exit 1
}

tag="$(sed -n 's/^CRAWLER_IMAGE_TAG=//p' "$DEPLOY_ENV" | tail -n1)"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "ERROR: deployed crawler image tag is missing or invalid" >&2
  exit 1
}
image="ghcr.io/colophon-group/jobseek-crawler:${tag}"

TOKEN_FILE="$(mktemp /run/lock/jobseek-ats-inventory-token.XXXXXX)"
chmod 0600 "$TOKEN_FILE"
/usr/local/sbin/jobseek-ats-inventory-github-token \
  --credentials-dir "$CREDENTIALS_DIRECTORY" \
  --output "$TOKEN_FILE"
RUN_LOG="$(mktemp /run/lock/jobseek-ats-inventory-log.XXXXXX)"
chmod 0600 "$RUN_LOG"
started_at="$(date +%s)"

if docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
  echo "ERROR: ATS inventory container already exists" >&2
  exit 1
fi
docker rm "$CONTAINER" >/dev/null 2>&1 || true

set +e
timeout --foreground --signal=TERM --kill-after=90s 3h docker run --rm \
  --name "$CONTAINER" \
  --init \
  --stop-timeout 60 \
  --network host \
  --memory 1536m \
  --cpus 1.0 \
  --pids-limit 256 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  --mount "type=bind,src=$STATE_ROOT/cache,dst=/state/cache" \
  --mount "type=bind,src=$TOKEN_FILE,dst=/run/credentials/github-token,readonly" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  --label com.docker.compose.project=deploy \
  --label com.docker.compose.service=ats-inventory \
  --label com.docker.compose.oneoff=True \
  --label jobseek.maintenance.operation=ats-inventory \
  --label jobseek.maintenance.issue=6190 \
  --label "jobseek.maintenance.revision=${revision}" \
  --label jobseek.maintenance.budget-seconds=10800 \
  "$image" \
  /app/.venv/bin/crawler ats-inventory \
    --cache-dir /state/cache \
    --impact \
    --candidate-issues "$effective_mode" \
    --queue-rollout-cap "$rollout_cap" \
    --github-token-file /run/credentials/github-token \
    --max-cache-mib 256 \
    --impact-max-cache-mib 768 \
    --impact-max-artifact-mib 512 \
    --impact-free-reserve-mib 1024 \
  2>&1 | tee "$RUN_LOG"
run_status=${PIPESTATUS[0]}
set -e

/usr/local/sbin/jobseek-ats-inventory-status \
  --state-root "$STATE_ROOT" \
  --log "$RUN_LOG" \
  --return-code "$run_status" \
  --requested-mode "$requested_mode" \
  --effective-mode "$effective_mode" \
  --rollout-cap "$rollout_cap" \
  --started-at "$started_at"
exit "$run_status"

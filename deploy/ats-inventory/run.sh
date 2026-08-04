#!/usr/bin/env bash
# Run one bounded data-only ATS inventory refresh and queue pass.
set -euo pipefail
umask 077

STATE_ROOT=/var/lib/jobseek-ats-inventory
CONFIG=/etc/jobseek-ats-inventory/config.env
WRITE_DISABLED=/etc/jobseek-ats-inventory/writes-disabled
DEPLOY_SUCCESS=/home/deploy/.crawler-deploy-success.env
ACCEPTANCE_PIN="$STATE_ROOT/acceptance-crawler.env"
CACHE_ROOT="$STATE_ROOT/cache"
CONTAINER=jobseek-ats-inventory
TOKEN_FILE=""
RUN_LOG=""
requested_mode=report
effective_mode=report
rollout_cap=1
started_at=0
STATUS_ARMED=0

cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  if command -v docker >/dev/null && docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  [[ -z "$TOKEN_FILE" ]] || rm -f -- "$TOKEN_FILE"
  if (( STATUS_ARMED )); then
    /usr/local/sbin/jobseek-ats-inventory-status \
      --state-root "$STATE_ROOT" \
      --log "$RUN_LOG" \
      --return-code "$status" \
      --requested-mode "$requested_mode" \
      --effective-mode "$effective_mode" \
      --rollout-cap "$rollout_cap" \
      --started-at "$started_at" || {
        (( status != 0 )) || status=1
      }
  fi
  [[ -z "$RUN_LOG" ]] || rm -f -- "$RUN_LOG"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for command in awk date docker flock grep mktemp openssl python3 sed sha256sum tail timeout tr; do
  command -v "$command" >/dev/null || {
    echo "ERROR: required command ${command} is unavailable" >&2
    exit 1
  }
done
exec 9>/run/lock/jobseek-ats-inventory-host.lock
flock -n 9 || {
  echo "ERROR: another ATS inventory host run is active" >&2
  exit 75
}
RUN_LOG="$(mktemp /run/lock/jobseek-ats-inventory-log.XXXXXX)"
chmod 0600 "$RUN_LOG"
started_at="$(date +%s)"
STATUS_ARMED=1

[[ -x /usr/local/sbin/jobseek-ats-inventory-status ]] || {
  echo "ERROR: ATS inventory status helper is unavailable" >&2
  exit 1
}
[[ -x /usr/local/sbin/jobseek-ats-inventory-bounded-tee ]] || {
  echo "ERROR: ATS inventory bounded logger is unavailable" >&2
  exit 1
}
[[ -r "$CONFIG" ]] || { echo "ERROR: ATS inventory configuration is unavailable" >&2; exit 1; }
[[ -n "${CREDENTIALS_DIRECTORY:-}" && -d "$CREDENTIALS_DIRECTORY" ]] || {
  echo "ERROR: GitHub App systemd credentials are unavailable" >&2
  exit 1
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

read_exact_release() {
  local path="$1" key="$2"
  mapfile -t matches < <(sed -n "s/^${key}=//p" "$path")
  [[ ${#matches[@]} -eq 1 && -n "${matches[0]}" ]] || {
    echo "ERROR: ${key} must appear exactly once in ${path}" >&2
    exit 1
  }
  printf '%s' "${matches[0]}"
}

configured_mode="$(read_exact_config ATS_INVENTORY_MODE)"
configured_cap="$(read_exact_config ATS_INVENTORY_ROLLOUT_CAP)"
case "$configured_mode" in
  report|dry-run|refill) requested_mode="$configured_mode" ;;
  *) echo "ERROR: invalid ATS inventory mode" >&2; exit 1 ;;
esac
case "$configured_cap" in
  1|5|25) rollout_cap="$configured_cap" ;;
  *) echo "ERROR: invalid ATS inventory rollout cap" >&2; exit 1 ;;
esac
effective_mode="$requested_mode"

apply_write_gate() {
  if [[ -e "$WRITE_DISABLED" || -e "$ACCEPTANCE_PIN" ]]; then
    effective_mode=report
    printf '%s\n' \
      '{"event":"ats_inventory.writes_disabled","effective_mode":"report"}'
  fi
}
apply_write_gate

revision="$(tr -d '\n' <"$STATE_ROOT/deployed-sha")"
wrapper_sha="$(tr -d '\n' <"$STATE_ROOT/wrapper-sha256")"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo "ERROR: invalid deployment revision" >&2; exit 1; }
[[ "$wrapper_sha" =~ ^[0-9a-f]{64}$ ]] || { echo "ERROR: invalid wrapper digest" >&2; exit 1; }
[[ "$(sha256sum /usr/local/sbin/jobseek-ats-inventory | awk '{print $1}')" == "$wrapper_sha" ]] || {
  echo "ERROR: installed ATS inventory wrapper digest drifted" >&2
  exit 1
}

release_file="$DEPLOY_SUCCESS"
if [[ -e "$ACCEPTANCE_PIN" ]]; then
  release_file="$ACCEPTANCE_PIN"
  CACHE_ROOT="$STATE_ROOT/acceptance-cache"
fi
[[ -r "$release_file" ]] || {
  echo "ERROR: committed crawler deployment marker is unavailable" >&2
  exit 1
}
tag="$(read_exact_release "$release_file" CRAWLER_IMAGE_TAG)"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][a-zA-Z0-9.]+)?$ ]] || {
  echo "ERROR: committed crawler image tag is invalid" >&2
  exit 1
}
crawler_revision="$(read_exact_release "$release_file" JOBSEEK_DEPLOY_REVISION)"
[[ "$crawler_revision" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ERROR: committed crawler deployment revision is invalid" >&2
  exit 1
}
image="ghcr.io/colophon-group/jobseek-crawler:${tag}"

if docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
  echo "ERROR: ATS inventory container already exists" >&2
  exit 1
fi
docker rm "$CONTAINER" >/dev/null 2>&1 || true

run_phase() {
  local budget="$1" phase="$2" candidate_mode="$3" use_token="$4"
  local -a docker_extra=() crawler_extra=(--candidate-issues "$candidate_mode")
  if [[ "$use_token" == 1 ]]; then
    docker_extra+=(--mount "type=bind,src=$TOKEN_FILE,dst=/run/credentials/github-token,readonly")
    crawler_extra+=(--github-token-file /run/credentials/github-token)
  fi
  timeout --foreground --signal=TERM --kill-after=90s "$budget" docker run --rm \
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
    --mount "type=bind,src=$CACHE_ROOT,dst=/state/cache" \
    "${docker_extra[@]}" \
    -e PYTHONDONTWRITEBYTECODE=1 \
    --label jobseek.maintenance.operation=ats-inventory \
    --label "jobseek.maintenance.phase=${phase}" \
    --label jobseek.maintenance.issue=6190 \
    --label "jobseek.maintenance.revision=${revision}" \
    --label jobseek.maintenance.budget-seconds=12600 \
    "$image" \
    /app/.venv/bin/crawler ats-inventory \
      --cache-dir /state/cache \
      --impact \
      --queue-rollout-cap "$rollout_cap" \
      --max-cache-mib 256 \
      --impact-max-cache-mib 768 \
      --impact-max-artifact-mib 512 \
      --impact-free-reserve-mib 1024 \
      "${crawler_extra[@]}" \
    2>&1 | /usr/local/sbin/jobseek-ats-inventory-bounded-tee \
      --output "$RUN_LOG" --max-bytes 16777216
  local -a pipeline_status=("${PIPESTATUS[@]}")
  local phase_status="${pipeline_status[0]}"
  if (( phase_status == 0 && pipeline_status[1] != 0 )); then
    phase_status="${pipeline_status[1]}"
  fi
  return "$phase_status"
}

# Refresh and verify the large data artifacts before minting the one-hour
# installation token. The second pass reuses the exact checksum-addressed
# impact cache and keeps all GitHub work inside a short budget.
set +e
run_phase 9900s data off 0
run_status=$?
set -e
(( run_status == 0 )) || exit "$run_status"

TOKEN_FILE="$(mktemp /run/lock/jobseek-ats-inventory-token.XXXXXX)"
chmod 0600 "$TOKEN_FILE"
/usr/local/sbin/jobseek-ats-inventory-github-token \
  --credentials-dir "$CREDENTIALS_DIRECTORY" \
  --output "$TOKEN_FILE"

# The root control may have disabled writes while the data phase ran. Recheck
# immediately before the only container that can reach GitHub issue APIs.
apply_write_gate
set +e
run_phase 2700s github "$effective_mode" 1
run_status=$?
set -e
exit "$run_status"

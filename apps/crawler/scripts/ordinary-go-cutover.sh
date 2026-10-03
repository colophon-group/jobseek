#!/usr/bin/env bash
# First native ordinary owner at the already selected B0 incarnation.
set -euo pipefail
umask 077
DEPLOY_DIR=/home/deploy
RECEIPT="$DEPLOY_DIR/.ordinary-go-owner-v1"
REQUEST="$DEPLOY_DIR/.ordinary-go-request-v1.json"
B0_RECEIPT="$DEPLOY_DIR/.lightpanda-b0-active-v1"
LOCK=/run/lock/jobseek-crawler-mutation.lock

reject() { echo "ERROR: native ordinary cutover rejected: $1" >&2; exit 1; }
bounded() { timeout --foreground --signal=TERM --kill-after=5s "$@"; }
value() {
  local path=$1 key=$2
  local -a rows=()
  [[ -f "$path" && ! -L "$path" ]] || return 1
  mapfile -t rows < <(sed -n "s/^${key}=//p" "$path")
  ((${#rows[@]} == 1)) || return 1
  printf '%s\n' "${rows[0]}"
}
protected() {
  [[ -f "$1" && ! -L "$1" && "$(stat -c '%u:%a:%h' "$1")" == "$(id -u):600:1" ]]
}
publish() {
  local path=$1 temp
  temp="$(mktemp "$path.tmp.XXXXXX")"
  cat >"$temp"
  chmod 600 "$temp"
  # Linux sync -f acknowledges the filesystem containing the file/directory.
  sync -f "$temp"
  mv -f -- "$temp" "$path"
  sync -f "$DEPLOY_DIR"
}

[[ $# -ge 1 && "$(id -un)" == deploy ]] || reject "run as deploy with stage, activate, retire or recover-pending"
operation=$1
case "$operation" in
  stage) [[ $# == 2 && "$2" == /* && -f "$2" && ! -L "$2" ]] || reject "stage requires a protected absolute cohort file" ;;
  activate) [[ $# == 3 && "$2" =~ ^[0-9a-f]{64}$ && "$3" =~ ^[0-9a-f]{40}$ ]] || reject "activate requires exact plan and projection hashes" ;;
  retire|recover-pending) [[ $# == 1 ]] || reject "retirement takes the retained identity" ;;
  *) reject "unknown operation" ;;
esac
[[ -d "$DEPLOY_DIR" && ! -L "$DEPLOY_DIR" && -f "$DEPLOY_DIR/.env" && ! -L "$DEPLOY_DIR/.env" ]] || reject "deployment is unavailable"
exec 9>"$LOCK"
flock -n 9 || reject "crawler mutation lock is held"
cd "$DEPLOY_DIR"
set -a
# shellcheck disable=SC1090,SC1091
source .env
set +a
unset WEBSHARE_PROXY_URLS
[[ "${JOBSEEK_DEPLOY_REVISION:-}" =~ ^[0-9a-f]{40}$ && "${CRAWLER_IMAGE_REF:-}" =~ ^ghcr\.io/[^/]+/jobseek-crawler@sha256:[0-9a-f]{64}$ ]] || reject "immutable release identity is missing"
[[ "${BROWSER_IMAGE_REF:-}" =~ ^ghcr\.io/[^/]+/jobseek-crawler-browser@sha256:[0-9a-f]{64}$ && "${LIGHTPANDA_B0_SERVICE_HOST:-}" == 10.0.0.5 ]] || reject "browser image or B0 host identity is invalid"
for key in ORDINARY_OWNERSHIP_SOURCE_REVISION ORDINARY_OWNERSHIP_ROUTING_EPOCH ORDINARY_OWNERSHIP_PLAN_SHA256 ORDINARY_OWNERSHIP_PROJECTION_SHA1; do
  [[ -z "${!key:-}" ]] || reject "base environment unexpectedly selects ordinary ownership"
done
protected "$B0_RECEIPT" || reject "active B0 receipt is unsafe"
[[ "$(wc -l <"$B0_RECEIPT" | tr -d '[:space:]')" == 11 && "$(value "$B0_RECEIPT" schema)" == jobseek.lightpanda-b0-active/v1 && "$(value "$B0_RECEIPT" state)" == active && "$(value "$B0_RECEIPT" crawler_image_ref)" == "$CRAWLER_IMAGE_REF" && "$(value "$B0_RECEIPT" deploy_revision)" == "$JOBSEEK_DEPLOY_REVISION" ]] || reject "B0 receipt does not select this image/source"
[[ "$(value "$B0_RECEIPT" plan_digest)" =~ ^[0-9a-f]{64}$ && "$(value "$B0_RECEIPT" activated_at_epoch)" =~ ^[1-9][0-9]*$ ]] || reject "B0 receipt is malformed"
LIGHTPANDA_B0_QUEUE_NAMESPACE="$(value "$B0_RECEIPT" namespace)"
LIGHTPANDA_B0_SHARD_ID="$(value "$B0_RECEIPT" shard_id)"
LIGHTPANDA_B0_ROUTING_EPOCH="$(value "$B0_RECEIPT" routing_epoch)"
LIGHTPANDA_B0_PRODUCER_COHORT="$(value "$B0_RECEIPT" cohort)"
export LIGHTPANDA_B0_QUEUE_NAMESPACE LIGHTPANDA_B0_SHARD_ID LIGHTPANDA_B0_ROUTING_EPOCH LIGHTPANDA_B0_PRODUCER_COHORT
[[ "$LIGHTPANDA_B0_QUEUE_NAMESPACE" == production-b0 && "$LIGHTPANDA_B0_SHARD_ID" == lightpanda-b0 && "$LIGHTPANDA_B0_ROUTING_EPOCH" =~ ^[1-9][0-9]{0,12}$ && "$LIGHTPANDA_B0_PRODUCER_COHORT" =~ ^(c1|c2|c3|cdom)$ ]] || reject "B0 tuple is invalid"
((LIGHTPANDA_B0_ROUTING_EPOCH <= 9999999999999)) || reject "B0 epoch is invalid"
b0_hash="$(sha256sum "$B0_RECEIPT" | awk '{print $1}')"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-deploy}"
compose=(docker compose --profile ordinary-go -f docker-compose.yml -f lightpanda-b0-enabled.override.yml)
bounded 30s "${compose[@]}" config -q
compose_hash="$(bounded 30s "${compose[@]}" config | sha256sum | awk '{print $1}')"
# The extra profile's unselected service changes the rendered contract. Compare
# the B0 receipt with the same base profile it was activated with.
b0_compose_hash="$(bounded 30s docker compose -f docker-compose.yml -f lightpanda-b0-enabled.override.yml config | sha256sum | awk '{print $1}')"
[[ "$(value "$B0_RECEIPT" compose_digest)" == "$b0_compose_hash" ]] || reject "B0 Compose contract drifted"
export ORDINARY_OWNERSHIP_SOURCE_REVISION="$JOBSEEK_DEPLOY_REVISION"
export ORDINARY_OWNERSHIP_ROUTING_EPOCH="$LIGHTPANDA_B0_ROUTING_EPOCH"
native=("${compose[@]}" run --rm --no-deps --entrypoint /usr/local/bin/go-ordinary-worker)
identity="$(bounded 30s "${native[@]}" ordinary-go --identity)"
[[ "$identity" == *"\"source_revision\":\"$JOBSEEK_DEPLOY_REVISION\""* && "$identity" == *'"profile":"greenhouse.token-skip/v1"'* ]] || reject "installed native executable differs"

if [[ "$operation" == stage ]]; then
  [[ ! -e "$RECEIPT" && ! -L "$RECEIPT" ]] || reject "ordinary ownership is already pending or active"
  bounded 45s "${native[@]}" \
    -v "$2:/run/jobseek/ordinary-cohort.json:ro" \
    -e ORDINARY_GO_WORKER_MODE=stage-ownership \
    -e ORDINARY_OWNERSHIP_SOURCE_REVISION -e ORDINARY_OWNERSHIP_ROUTING_EPOCH \
    -e ORDINARY_OWNERSHIP_PLAN_SHA256= -e ORDINARY_OWNERSHIP_PROJECTION_SHA1= \
    -e ORDINARY_GO_COHORT_FILE=/run/jobseek/ordinary-cohort.json \
    ordinary-go --stage-ownership
  exit 0
fi

state=""
if [[ -e "$RECEIPT" || -L "$RECEIPT" ]]; then
  protected "$RECEIPT" || reject "ordinary receipt is unsafe"
  [[ "$(wc -l <"$RECEIPT" | tr -d '[:space:]')" == 9 && "$(value "$RECEIPT" schema)" == jobseek.ordinary.first-owner/v1 && "$(value "$RECEIPT" source_revision)" == "$JOBSEEK_DEPLOY_REVISION" && "$(value "$RECEIPT" crawler_image_ref)" == "$CRAWLER_IMAGE_REF" && "$(value "$RECEIPT" routing_epoch)" == "$LIGHTPANDA_B0_ROUTING_EPOCH" && "$(value "$RECEIPT" b0_receipt_sha256)" == "$b0_hash" && "$(value "$RECEIPT" compose_sha256)" == "$compose_hash" ]] || reject "ordinary receipt identity drifted"
  state="$(value "$RECEIPT" state)"
  ORDINARY_OWNERSHIP_PLAN_SHA256="$(value "$RECEIPT" plan_sha256)"
  ORDINARY_OWNERSHIP_PROJECTION_SHA1="$(value "$RECEIPT" projection_sha1)"
  export ORDINARY_OWNERSHIP_PLAN_SHA256 ORDINARY_OWNERSHIP_PROJECTION_SHA1
  [[ "$ORDINARY_OWNERSHIP_PLAN_SHA256" =~ ^[0-9a-f]{64}$ && "$ORDINARY_OWNERSHIP_PROJECTION_SHA1" =~ ^[0-9a-f]{40}$ && "$state" =~ ^(pending|active|retiring)$ ]] || reject "ordinary receipt is malformed"
  [[ "$operation" != activate || ("$2" == "$ORDINARY_OWNERSHIP_PLAN_SHA256" && "$3" == "$ORDINARY_OWNERSHIP_PROJECTION_SHA1" && "$state" == pending) ]] || reject "activation retry differs from pending identity"
else
  [[ "$operation" == activate ]] || reject "retirement requires its retained identity"
  export ORDINARY_OWNERSHIP_PLAN_SHA256=$2 ORDINARY_OWNERSHIP_PROJECTION_SHA1=$3
fi
[[ "$operation" != retire || "$state" == active || "$state" == retiring ]] || reject "use recover-pending to cancel an incomplete activation"
[[ "$operation" != recover-pending || "$state" == pending || "$state" == retiring ]] || reject "no pending ordinary activation"

write_receipt() {
  printf '%s\n' 'schema=jobseek.ordinary.first-owner/v1' "state=$1" \
    "source_revision=$JOBSEEK_DEPLOY_REVISION" "crawler_image_ref=$CRAWLER_IMAGE_REF" \
    "routing_epoch=$LIGHTPANDA_B0_ROUTING_EPOCH" "plan_sha256=$ORDINARY_OWNERSHIP_PLAN_SHA256" \
    "projection_sha1=$ORDINARY_OWNERSHIP_PROJECTION_SHA1" "b0_receipt_sha256=$b0_hash" \
    "compose_sha256=$compose_hash" | publish "$RECEIPT"
}
writers=(worker-1 worker-2 worker-3 browser-1 exporter drain lightpanda-producer lightpanda-executor lightpanda-claimant ordinary-go)
services=(worker-1 worker-2 worker-3 browser-1 exporter drain lightpanda-producer lightpanda-executor lightpanda-claimant)
armed=0
contain() {
  local status=$?
  trap - EXIT
  if ((status != 0 && armed)); then
    for service in "${writers[@]}"; do
      ids="$(bounded 15s "${compose[@]}" ps -aq "$service")" || ids=""
      while IFS= read -r id; do
        [[ "$id" =~ ^[0-9a-f]{12,64}$ ]] || continue
        bounded 15s docker update --restart no "$id" >/dev/null || true
      done <<<"$ids"
    done
    bounded 90s "${compose[@]}" stop --timeout 60 "${writers[@]}" || true
    bounded 30s "${compose[@]}" kill "${writers[@]}" || true
    oneoffs="$(bounded 15s docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --filter label=com.docker.compose.oneoff=True)" || oneoffs=""
    while IFS= read -r id; do
      [[ "$id" =~ ^[0-9a-f]{12,64}$ ]] || continue
      bounded 15s docker kill "$id" >/dev/null || true
    done <<<"$oneoffs"
    echo "ERROR: ordinary cutover failed; pending identity retained and crawler lane contained" >&2
  fi
  exit "$status"
}
trap contain EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Arm recovery before any writer stop or restart-policy effect.
armed=1
if [[ "$operation" == activate ]]; then write_receipt pending; else write_receipt retiring; fi
oneoffs="$(bounded 15s docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --filter label=com.docker.compose.oneoff=True)"
[[ -z "$oneoffs" ]] || reject "existing Compose one-off prevents quiescence"
cold_inventory=""
for service in "${writers[@]}"; do
  ids="$(bounded 15s "${compose[@]}" ps -aq "$service")"
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    [[ "$id" =~ ^[0-9a-f]{12,64}$ ]] || reject "invalid container identity"
    bounded 15s docker update --restart no "$id" >/dev/null
  done <<<"$ids"
done
bounded 90s "${compose[@]}" stop --timeout 60 "${writers[@]}"
for service in "${writers[@]}"; do
  ids="$(bounded 15s "${compose[@]}" ps -aq "$service")"
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    observation="$(bounded 15s docker inspect -f '{{.State.Running}}:{{.HostConfig.RestartPolicy.Name}}:{{.Config.Image}}' "$id")"
    [[ "$observation" == false:no:* ]] || reject "writer is not stopped and restart-disabled"
    expected_image=$CRAWLER_IMAGE_REF
    [[ "$service" != browser-1 ]] || expected_image=$BROWSER_IMAGE_REF
    [[ "$observation" == "false:no:$expected_image" ]] || reject "writer image differs from the selected release"
    cold_inventory+="$service:$id:$observation"$'\n'
  done <<<"$ids"
done
[[ "$(sha256sum "$B0_RECEIPT" | awk '{print $1}')" == "$b0_hash" ]] || reject "B0 receipt changed"
running_services="$(bounded 15s docker ps --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --format '{{.Label "com.docker.compose.service"}}')"
while IFS= read -r service; do
  [[ -z "$service" || "$service" == redis || "$service" == alloy ]] || reject "unexpected live container prevents cold ownership mutation"
done <<<"$running_services"
cold_hash="$(printf '%s' "$cold_inventory" | sha256sum | awk '{print $1}')"
effect=activate
[[ "$operation" == activate ]] || effect=retire
printf '{"version":"jobseek.ordinary.first-owner-request/v1","operation":"%s","source_revision":"%s","routing_epoch":%s,"plan_sha256":"%s","projection_sha1":"%s","crawler_image_ref":"%s","b0_receipt_sha256":"%s","b0_cohort":"%s","namespace":"production-b0","shard_id":"lightpanda-b0","cold_host_sha256":"%s"}\n' \
  "$effect" "$JOBSEEK_DEPLOY_REVISION" "$LIGHTPANDA_B0_ROUTING_EPOCH" "$ORDINARY_OWNERSHIP_PLAN_SHA256" "$ORDINARY_OWNERSHIP_PROJECTION_SHA1" "$CRAWLER_IMAGE_REF" "$b0_hash" "$LIGHTPANDA_B0_PRODUCER_COHORT" "$cold_hash" | publish "$REQUEST"
# Ten-minute legacy SQL leases can survive graceful process cancellation. Keep
# the same cold host and pending identity while the native command observes
# their natural expiry, then rechecks all exclusive SQL barriers.
bounded 860s "${native[@]}" -v "$REQUEST:/run/jobseek/ordinary-request.json:ro" \
  -e "ORDINARY_GO_WORKER_MODE=$effect-first-ownership" \
  -e ORDINARY_FIRST_OWNERSHIP_REQUEST_FILE=/run/jobseek/ordinary-request.json \
  -e ORDINARY_OWNERSHIP_SOURCE_REVISION -e ORDINARY_OWNERSHIP_ROUTING_EPOCH \
  -e ORDINARY_OWNERSHIP_PLAN_SHA256 -e ORDINARY_OWNERSHIP_PROJECTION_SHA1 \
  -e CRAWLER_IMAGE_REF -e LIGHTPANDA_B0_ROUTING_EPOCH \
  -e LIGHTPANDA_B0_QUEUE_NAMESPACE -e LIGHTPANDA_B0_SHARD_ID -e LIGHTPANDA_B0_PRODUCER_COHORT \
  ordinary-go "--$effect-first-ownership"

if [[ "$effect" == activate ]]; then
  services+=(ordinary-go)
else
  unset ORDINARY_OWNERSHIP_SOURCE_REVISION ORDINARY_OWNERSHIP_ROUTING_EPOCH ORDINARY_OWNERSHIP_PLAN_SHA256 ORDINARY_OWNERSHIP_PROJECTION_SHA1
fi
# Keep every restarted service unable to self-restart until full readiness.
restart_override="$(mktemp "$DEPLOY_DIR/.ordinary-go-restart-disabled.XXXXXX")"
printf 'services:\n' >"$restart_override"
for service in "${services[@]}"; do printf '  %s:\n    restart: "no"\n' "$service" >>"$restart_override"; done
bounded 90s "${compose[@]}" -f "$restart_override" up -d --force-recreate "${services[@]}"
for service in "${services[@]}"; do
  healthy=0
  for ((attempt=1; attempt<=48; attempt++)); do
    id="$(bounded 15s "${compose[@]}" ps -q "$service")"
    [[ "$id" =~ ^[0-9a-f]{64}$ ]] || reject "service does not resolve to one container"
    expected_image=$CRAWLER_IMAGE_REF
    [[ "$service" != browser-1 ]] || expected_image=$BROWSER_IMAGE_REF
    [[ "$(bounded 15s docker inspect -f '{{.Config.Image}}' "$id")" == "$expected_image" ]] || reject "restarted service image differs from the selected release"
    status="$(bounded 15s docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")"
    if [[ "$status" == healthy || "$status" == running ]]; then healthy=1; break; fi
    sleep 5
  done
  ((healthy)) || reject "complete stack readiness failed"
done
for endpoint in 9095/ 9096/ 9097/ 9098/ 9093/health 9094/health 9101/healthz; do
  bounded 10s curl --silent --show-error --fail --max-time 5 "http://127.0.0.1:$endpoint" >/dev/null
done
if [[ "$effect" == activate ]]; then
  bounded 10s curl --silent --show-error --fail --max-time 5 http://127.0.0.1:9104/healthz >/dev/null
fi
if [[ "$effect" == activate ]]; then write_receipt active; fi
for service in "${services[@]}"; do
  id="$(bounded 15s "${compose[@]}" ps -q "$service")"
  bounded 15s docker update --restart unless-stopped "$id" >/dev/null
  [[ "$(bounded 15s docker inspect -f '{{.State.Running}}:{{.HostConfig.RestartPolicy.Name}}' "$id")" == true:unless-stopped ]] || reject "restart arming failed"
done
if [[ "$effect" == retire ]]; then
  # The retired native container is stopped and restart-disabled. Leaving it
  # behind makes the next release's cold image check see the prior image and
  # refuse activation. Remove only this exact service container, preserving
  # its immutable image and volumes for the supported rollback generation.
  ids="$(bounded 15s "${compose[@]}" ps -aq ordinary-go)"
  if [[ -n "$ids" ]]; then
    [[ "$ids" =~ ^[0-9a-f]{64}$ ]] || reject "retired native service cardinality differs"
    observation="$(bounded 15s docker inspect -f '{{.State.Running}}:{{.HostConfig.RestartPolicy.Name}}:{{.Config.Image}}' "$ids")"
    [[ "$observation" == "false:no:$CRAWLER_IMAGE_REF" ]] || reject "retired native container is not cold at this image"
    bounded 15s docker rm "$ids" >/dev/null
  fi
  rm -f -- "$RECEIPT"
  sync -f "$DEPLOY_DIR"
fi
rm -f -- "$restart_override"
sync -f "$DEPLOY_DIR"
armed=0
echo "Native ordinary owner $effect complete at unchanged B0 epoch $LIGHTPANDA_B0_ROUTING_EPOCH"

#!/usr/bin/env bash
set -euo pipefail
umask 077

action="${1:-}"
revision="${2:-}"
[[ "$action" = stage || "$action" = activate || "$action" = tunnel || "$action" = verify ]]
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$(git rev-parse HEAD)" = "$revision" ]]
: "${TARGET_HOST:?}" "${SSH_PRIVATE_KEY:?}" "${SSH_KNOWN_HOSTS:?}" "${RUNNER_TEMP:?}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.:_-]+$ ]]

local_stage="$(mktemp -d "$RUNNER_TEMP/jobseek-web-origin.XXXXXXXX")"
remote_stage=""
options=(
  -F /dev/null -i "$local_stage/id" -o BatchMode=yes -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$local_stage/known_hosts"
  -o GlobalKnownHostsFile=/dev/null -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no -o ServerAliveInterval=15
  -o ServerAliveCountMax=3 -o LogLevel=ERROR
)
cleanup() {
  if [[ -n "$remote_stage" ]]; then
    # shellcheck disable=SC2029 # The remote path is checked against a strict /run pattern.
    ssh "${options[@]}" "root@$TARGET_HOST" "rm -rf -- '$remote_stage'" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$local_stage"
}
trap cleanup EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$local_stage/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$local_stage/known_hosts"
chmod 0600 "$local_stage/id" "$local_stage/known_hosts"
ssh-keygen -y -f "$local_stage/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$local_stage/known_hosts" >/dev/null

if [[ "$action" = activate ]]; then
  timeout --foreground 180s ssh "${options[@]}" "root@$TARGET_HOST" \
    "bash /usr/local/lib/jobseek-web/activate-host.sh '$revision'"
  exit
fi

if [[ "$action" = verify ]]; then
  timeout --foreground 30s ssh "${options[@]}" "root@$TARGET_HOST" \
    "bash /usr/local/lib/jobseek-web/verify-host.sh '$revision'"
  exit
fi

if [[ "$action" = tunnel ]]; then
  : "${WEB_ORIGIN_TUNNEL_TOKEN:?}"
  [[ "$WEB_ORIGIN_TUNNEL_TOKEN" =~ ^[A-Za-z0-9._-]+={0,2}$ ]]
  printf '%s' "$WEB_ORIGIN_TUNNEL_TOKEN" > "$local_stage/tunnel-token"
  chmod 0600 "$local_stage/tunnel-token"
  remote_stage="$(ssh "${options[@]}" "root@$TARGET_HOST" 'mktemp -d /run/jobseek-web-stage.XXXXXXXX')"
  [[ "$remote_stage" =~ ^/run/jobseek-web-stage\.[A-Za-z0-9]+$ ]]
  scp "${options[@]}" "$local_stage/tunnel-token" \
    deploy/web-origin/install-tunnel-host.sh deploy/web-origin/jobseek-web-tunnel.service \
    "root@$TARGET_HOST:$remote_stage/"
  timeout --foreground 300s ssh "${options[@]}" "root@$TARGET_HOST" \
    "bash '$remote_stage/install-tunnel-host.sh' '$remote_stage/tunnel-token'"
  exit
fi

: "${WEB_ORIGIN_ARCHIVE:?}" "${WEB_ORIGIN_RUNTIME_JSON:?}"
test -s "$WEB_ORIGIN_ARCHIVE"
test -s "$WEB_ORIGIN_RUNTIME_JSON"
hash="$(sha256sum "$WEB_ORIGIN_ARCHIVE" | cut -d' ' -f1)"
remote_stage="$(ssh "${options[@]}" "root@$TARGET_HOST" 'mktemp -d /run/jobseek-web-stage.XXXXXXXX')"
[[ "$remote_stage" =~ ^/run/jobseek-web-stage\.[A-Za-z0-9]+$ ]]
scp "${options[@]}" "$WEB_ORIGIN_ARCHIVE" "root@$TARGET_HOST:$remote_stage/release.tar.gz"
scp "${options[@]}" "$WEB_ORIGIN_RUNTIME_JSON" "root@$TARGET_HOST:$remote_stage/runtime.json"
scp "${options[@]}" deploy/web-origin/stage-host.sh deploy/web-origin/activate-host.sh \
  deploy/web-origin/verify-host.sh \
  deploy/web-origin/launch.mjs deploy/web-origin/run-alerts.mjs \
  deploy/web-origin/jobseek-web@.service deploy/web-origin/jobseek-web-alerts.service \
  deploy/web-origin/jobseek-web-alerts.timer \
  "root@$TARGET_HOST:$remote_stage/"
timeout --foreground 300s ssh "${options[@]}" "root@$TARGET_HOST" \
  "bash '$remote_stage/stage-host.sh' '$revision' '$remote_stage/release.tar.gz' '$hash' '$remote_stage/runtime.json' && install -o root -g root -m 0700 '$remote_stage/activate-host.sh' /usr/local/lib/jobseek-web/activate-host.sh && install -o root -g root -m 0700 '$remote_stage/verify-host.sh' /usr/local/lib/jobseek-web/verify-host.sh"

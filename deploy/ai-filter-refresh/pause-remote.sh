#!/usr/bin/env bash
set -euo pipefail
umask 077
revision="${1:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$(git rev-parse HEAD)" = "$revision" ]]
: "${TARGET_HOST:?}" "${SSH_PRIVATE_KEY:?}" "${SSH_KNOWN_HOSTS:?}" "${RUNNER_TEMP:?}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.:_-]+$ ]]
stage="$(mktemp -d "$RUNNER_TEMP/jobseek-ai-filter-pause.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$stage/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$stage/known_hosts"
chmod 0600 "$stage/id" "$stage/known_hosts"
ssh-keygen -y -f "$stage/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$stage/known_hosts" >/dev/null
options=(
  -F /dev/null -i "$stage/id" -o BatchMode=yes -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$stage/known_hosts"
  -o GlobalKnownHostsFile=/dev/null -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no -o LogLevel=ERROR
)
timeout --foreground --signal=TERM --kill-after=10s 60s \
  ssh "${options[@]}" "root@$TARGET_HOST" 'set -eu
    test "$(systemctl show jobseek-ai-filter-refresh.timer -p LoadState --value)" = loaded
    systemctl disable --now jobseek-ai-filter-refresh.timer
    systemctl stop jobseek-ai-filter-refresh.service
    systemctl reset-failed jobseek-ai-filter-refresh.service
    test "$(systemctl is-enabled jobseek-ai-filter-refresh.timer || true)" = disabled
    test "$(systemctl is-active jobseek-ai-filter-refresh.timer || true)" = inactive
    test "$(systemctl is-active jobseek-ai-filter-refresh.service || true)" = inactive
    printf "%s\n" ai_filter_refresh_paused'

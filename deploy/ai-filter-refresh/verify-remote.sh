#!/usr/bin/env bash
set -euo pipefail
umask 077
revision="${1:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$(git rev-parse HEAD)" = "$revision" ]]
watchlist_id="${2:-}"
[[ "$watchlist_id" =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]]
: "${TARGET_HOST:?}" "${SSH_PRIVATE_KEY:?}" "${SSH_KNOWN_HOSTS:?}" "${RUNNER_TEMP:?}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.:_-]+$ ]]
stage="$(mktemp -d "$RUNNER_TEMP/jobseek-ai-filter-verify.XXXXXX")"
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
  ssh "${options[@]}" "root@$TARGET_HOST" "CREDENTIALS_DIRECTORY=/etc/jobseek-ai-filter-refresh /usr/bin/python3 - --inspect '$watchlist_id'" \
    < scripts/ai-filter-refresh.py

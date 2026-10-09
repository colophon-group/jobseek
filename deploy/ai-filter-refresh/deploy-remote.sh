#!/usr/bin/env bash
set -euo pipefail
umask 077
revision="${1:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$(git rev-parse HEAD)" = "$revision" ]]
: "${TARGET_HOST:?}" "${SSH_PRIVATE_KEY:?}" "${SSH_KNOWN_HOSTS:?}" "${RUNNER_TEMP:?}" "${AI_FILTER_REFRESH_SECRET:?}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.:_-]+$ ]]
stage="$(mktemp -d "$RUNNER_TEMP/jobseek-ai-filter-refresh.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$stage/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$stage/known_hosts"
chmod 0600 "$stage/id" "$stage/known_hosts"
ssh-keygen -y -f "$stage/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$stage/known_hosts" >/dev/null
printf '%s\n' "$AI_FILTER_REFRESH_SECRET" > "$stage/refresh-bearer"
options=(
  -F /dev/null -i "$stage/id" -o BatchMode=yes -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$stage/known_hosts"
  -o GlobalKnownHostsFile=/dev/null -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no -o ServerAliveInterval=15
  -o ServerAliveCountMax=3 -o LogLevel=ERROR
)
bundle="/opt/jobseek-ai-filter-refresh-deploy/$revision"
tar -czf - scripts/ai-filter-refresh.py deploy/ai-filter-refresh/install-host.sh \
  deploy/systemd/jobseek-ai-filter-refresh.service deploy/systemd/jobseek-ai-filter-refresh.timer |
  timeout --foreground 120s ssh "${options[@]}" "root@$TARGET_HOST" \
    "install -d -o root -g root -m 0700 $bundle && tar -xzf - -C $bundle --no-same-owner --no-same-permissions && chown -R root:root $bundle && chmod -R go-w $bundle"
timeout --foreground --signal=TERM --kill-after=10s 240s \
  ssh "${options[@]}" "root@$TARGET_HOST" \
    "bash $bundle/deploy/ai-filter-refresh/install-host.sh $revision" < "$stage/refresh-bearer"

#!/usr/bin/env bash
set -euo pipefail
umask 077
revision="${1:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$(git rev-parse HEAD)" = "$revision" ]]
python3 -c 'import json; from pathlib import Path; controls = json.loads(Path("apps/web/src/config/service-availability.json").read_text()); assert controls["narrowedWorkerPaused"] is False, "Narrowed worker pause blocks timer activation"'
: "${TARGET_HOST:?}" "${SSH_PRIVATE_KEY:?}" "${SSH_KNOWN_HOSTS:?}" "${RUNNER_TEMP:?}" "${NARROWED_WORKER_ENV_FILE:?}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.:_-]+$ ]]
stage="$(mktemp -d "$RUNNER_TEMP/jobseek-ai-filter-refresh.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$stage/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$stage/known_hosts"
chmod 0600 "$stage/id" "$stage/known_hosts"
ssh-keygen -y -f "$stage/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$stage/known_hosts" >/dev/null
test -f "$NARROWED_WORKER_ENV_FILE"
manifest_hash="$(sha256sum deploy/ai-filter-refresh/bundle/manifest.json | cut -d' ' -f1)"
python3 deploy/ai-filter-refresh/verify-bundle.py deploy/ai-filter-refresh/bundle "$revision" "$manifest_hash"
options=(
  -F /dev/null -i "$stage/id" -o BatchMode=yes -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$stage/known_hosts"
  -o GlobalKnownHostsFile=/dev/null -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no -o ServerAliveInterval=15
  -o ServerAliveCountMax=3 -o LogLevel=ERROR
)
bundle="/opt/jobseek-ai-filter-refresh-deploy/$revision"
tar -czf - deploy/ai-filter-refresh/bundle deploy/ai-filter-refresh/install-host.sh \
  deploy/ai-filter-refresh/run-host.sh deploy/ai-filter-refresh/verify-bundle.py \
  deploy/systemd/jobseek-ai-filter-refresh.service deploy/systemd/jobseek-ai-filter-refresh.timer |
  timeout --foreground 120s ssh "${options[@]}" "root@$TARGET_HOST" \
    "install -d -o root -g root -m 0700 $bundle && tar -xzf - -C $bundle --no-same-owner --no-same-permissions && chown -R root:root $bundle && chmod -R go-w $bundle"
timeout --foreground --signal=TERM --kill-after=10s 300s \
  ssh "${options[@]}" "root@$TARGET_HOST" \
    "bash $bundle/deploy/ai-filter-refresh/install-host.sh $revision $manifest_hash" < "$NARROWED_WORKER_ENV_FILE"

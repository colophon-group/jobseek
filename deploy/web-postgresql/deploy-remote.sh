#!/usr/bin/env bash
# Exact-revision native OpenSSH; payloads and credentials never enter arguments.
set -euo pipefail
umask 077
revision=${1:-}
action=${2:-}
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$(git rev-parse HEAD)" = "$revision" ]]
[[ "$action" =~ ^(prepare|rehearse|copy|verify|verify-server|backup-source|restore-source|recover-repository|activate-backup)$ ]]
: "${TARGET_HOST:?}" "${SSH_PRIVATE_KEY:?}" "${SSH_KNOWN_HOSTS:?}" "${RUNNER_TEMP:?}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.:_-]+$ ]]
stage=$(mktemp -d "$RUNNER_TEMP/web-postgresql.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$stage/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$stage/known_hosts"
chmod 0600 "$stage/id" "$stage/known_hosts"
ssh-keygen -y -f "$stage/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$stage/known_hosts" >/dev/null
options=(-F /dev/null -i "$stage/id" -o BatchMode=yes -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$stage/known_hosts"
  -o GlobalKnownHostsFile=/dev/null -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no -o LogLevel=ERROR
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
release="/opt/jobseek-web-postgresql/releases/$revision"
# Install a content-addressed bundle, then verify the same manifest remotely.
find deploy/web-postgresql scripts/jobseek-ingress-conformance.py -type f ! -path '*/__pycache__/*' -print0 |
  sort -z | xargs -0 sha256sum > "$stage/SHA256SUMS"
tar -czf "$stage/bundle.tar.gz" --exclude='__pycache__' deploy/web-postgresql scripts/jobseek-ingress-conformance.py
archive_sha=$(sha256sum "$stage/bundle.tar.gz" | cut -d' ' -f1)
ssh "${options[@]}" "root@$TARGET_HOST" \
  "install -d -o root -g root -m 0700 $release && cat > $release/bundle.tar.gz" < "$stage/bundle.tar.gz"
ssh "${options[@]}" "root@$TARGET_HOST" \
  "cd $release && test \"\$(sha256sum bundle.tar.gz | cut -d' ' -f1)\" = $archive_sha && tar -xzf bundle.tar.gz --no-same-owner --no-same-permissions && chown -R root:root deploy && chmod -R go-w deploy && cat > SHA256SUMS" < "$stage/SHA256SUMS"
ssh "${options[@]}" "root@$TARGET_HOST" "cd $release && sha256sum --status -c SHA256SUMS"
helper="$release/deploy/web-postgresql"
case "$action" in
  prepare)
    : "${WEB_POSTGRES_OWNER_PASSWORD:?}" "${WEB_POSTGRES_AUDITOR_PASSWORD:?}"
    : "${NARROWED_WORKER_DATABASE_PASSWORD:?}" "${CLOUDFLARE_DNS_API_TOKEN:?}"
    : "${DATABASE_URL_UNPOOLED:?}" "${CRAWLER_PRIVATE_IPV4:?}" "${CRAWLER_PUBLIC_IPV4:?}"
    export DEPLOY_REVISION="$revision"
    python3 - <<'PY' | ssh "${options[@]}" "root@$TARGET_HOST" "python3 $helper/host.py prepare"
import json,os
print(json.dumps({
 'owner_password':os.environ['WEB_POSTGRES_OWNER_PASSWORD'],
 'auditor_password':os.environ['WEB_POSTGRES_AUDITOR_PASSWORD'],
 'worker_password':os.environ['NARROWED_WORKER_DATABASE_PASSWORD'],
 'cloudflare_token':os.environ['CLOUDFLARE_DNS_API_TOKEN'],
 'public_ipv4':os.environ['TARGET_HOST'],
 'crawler_private_ipv4':os.environ['CRAWLER_PRIVATE_IPV4'],
 'crawler_public_ipv4':os.environ['CRAWLER_PUBLIC_IPV4'],
 'source_url':os.environ['DATABASE_URL_UNPOOLED'],
 'revision':os.environ['DEPLOY_REVISION']}))
PY
    ;;
  rehearse|copy|verify|verify-server)
    ssh "${options[@]}" "root@$TARGET_HOST" "python3 $helper/host.py $action"
    ;;
  backup-source|restore-source|recover-repository)
    case "$action" in backup-source) mode=backup ;; restore-source) mode=restore ;; *) mode=recover-repository ;; esac
    ssh "${options[@]}" "root@$TARGET_HOST" "python3 $helper/repository.py $mode"
    ;;
  activate-backup)
    : "${DATABASE_URL_UNPOOLED:?}"
    printf '%s' "$DATABASE_URL_UNPOOLED" |
      ssh "${options[@]}" "root@$TARGET_HOST" \
      "install -d -o root -g root -m 0700 /run/jobseek-web-postgresql-backup && umask 077 && cat > /run/jobseek-web-postgresql-backup/activation.url && python3 $helper/repository.py activate --url-file /run/jobseek-web-postgresql-backup/activation.url"
    ;;
esac

#!/usr/bin/env bash
# Copy and install reviewed backup artifacts over host-key-pinned OpenSSH.
set -euo pipefail
umask 077

SERVICE="${1:-}"
DEPLOY_SHA="${2:-}"
case "$SERVICE" in
  postgresql|typesense|web-postgresql) ;;
  *) exit 2 ;;
esac
[[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 2
: "${TARGET_HOST:?TARGET_HOST is required}"
: "${SSH_PRIVATE_KEY:?SSH_PRIVATE_KEY is required}"
: "${SSH_KNOWN_HOSTS:?SSH_KNOWN_HOSTS is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.-]+$ ]] || exit 2

if [[ "$SERVICE" == "typesense" ]]; then
  : "${JOBSEEK_TYPESENSE_BACKUP_KEY:?Typesense backup key is required}"
elif [[ "$SERVICE" == "web-postgresql" ]]; then
  : "${JOBSEEK_WEB_DATABASE_URL:?web database URL is required}"
fi

ssh_root="$(mktemp -d "$RUNNER_TEMP/jobseek-backup-deploy-ssh.XXXXXX")"
payload="$(mktemp "$RUNNER_TEMP/jobseek-backup-deploy-payload.XXXXXX")"
cleanup() {
  rm -rf -- "$ssh_root"
  rm -f -- "$payload"
}
trap cleanup EXIT

printf '%s\n' "$SSH_PRIVATE_KEY" >"$ssh_root/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" >"$ssh_root/known_hosts"
chmod 0600 "$ssh_root/id" "$ssh_root/known_hosts" "$payload"
ssh-keygen -y -f "$ssh_root/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$ssh_root/known_hosts" >/dev/null
ssh_options=(
  -F /dev/null
  -i "$ssh_root/id"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$ssh_root/known_hosts"
  -o GlobalKnownHostsFile=/dev/null
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -o LogLevel=ERROR
)

artifact_paths=(
  scripts/jobseek-data-backup.py
  apps/web/drizzle/0086_drop_supabase_job_posting.sql
  deploy/backups
  deploy/systemd/jobseek-postgresql-backup.service
  deploy/systemd/jobseek-postgresql-backup.timer
  deploy/systemd/jobseek-postgresql-backup-repository.service
  deploy/systemd/jobseek-postgresql-emergency-headroom.service
  deploy/systemd/jobseek-typesense-backup.service
  deploy/systemd/jobseek-typesense-backup.timer
  deploy/systemd/jobseek-web-postgresql-backup.service
  deploy/systemd/jobseek-web-postgresql-backup.timer
  docs/19-data-backup-recovery.md
  docs/16-hetzner-maintenance.md
)

tar --create --gzip --file - "${artifact_paths[@]}" |
  timeout --foreground --signal=TERM --kill-after=30s 15m \
    ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
    "install -d -o root -g root -m 0755 /opt/jobseek-backup && \
      tar --extract --gzip --file - --directory /opt/jobseek-backup \
        --no-same-owner --no-same-permissions"

{
  printf '%s\n' "$DEPLOY_SHA"
  printf '%s\n' "$SERVICE"
  printf '%s' "${JOBSEEK_TYPESENSE_BACKUP_KEY:-}" | base64 | tr -d '\n'
  printf '\n'
  printf '%s' "${JOBSEEK_WEB_DATABASE_URL:-}" | base64 | tr -d '\n'
  printf '\n'
} >"$payload"

timeout --foreground --signal=TERM --kill-after=30s 330m \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
  "bash /opt/jobseek-backup/deploy/backups/install-host-from-stdin.sh" <"$payload"

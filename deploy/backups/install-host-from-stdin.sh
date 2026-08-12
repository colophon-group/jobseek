#!/usr/bin/env bash
# Receive one root-only deployment payload on stdin and install one service.
set -euo pipefail
umask 077

payload="$(mktemp /run/jobseek-backup-deploy-payload.XXXXXX)"
credential_root=""
cleanup() {
  trap - EXIT HUP INT TERM
  rm -f -- "$payload"
  if [[ -n "$credential_root" ]]; then
    rm -rf -- "$credential_root"
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cat >"$payload"
chown root:root "$payload"
chmod 0600 "$payload"

mapfile -t fields <"$payload"
[[ "${#fields[@]}" -eq 4 ]] || {
  echo "ERROR: invalid backup deployment payload" >&2
  exit 1
}
deploy_sha="${fields[0]}"
service="${fields[1]}"
[[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
case "$service" in
  postgresql|typesense|web-postgresql) ;;
  *) exit 2 ;;
esac

credential_root="$(mktemp -d /run/jobseek-backup-deploy-credentials.XXXXXX)"
typesense_key_file="$credential_root/typesense-key"
web_database_url_file="$credential_root/web-database-url"
printf '%s' "${fields[2]}" | base64 --decode >"$typesense_key_file"
printf '%s' "${fields[3]}" | base64 --decode >"$web_database_url_file"
chown root:root "$typesense_key_file" "$web_database_url_file"
chmod 0600 "$typesense_key_file" "$web_database_url_file"
unset fields
rm -f -- "$payload"

cd /opt/jobseek-backup
installer_args=("$service")
if [[ "$service" != "web-postgresql" ]]; then
  # The production deployment contract below requires the durable data-backup
  # timers to be enabled. Make that intent explicit so a fail-safe-disabled
  # timer can be recovered by the next reviewed deployment.
  installer_args=(--start-timer "$service")
fi
JOBSEEK_BACKUP_DEPLOY_SHA="$deploy_sha" \
JOBSEEK_TYPESENSE_BACKUP_KEY_FILE="$typesense_key_file" \
JOBSEEK_WEB_DATABASE_URL_FILE="$web_database_url_file" \
  bash deploy/backups/install-host.sh "${installer_args[@]}"

if systemctl is-enabled --quiet "jobseek-${service}-backup.timer"; then
  systemctl is-active "jobseek-${service}-backup.timer"
  if systemctl is-failed --quiet "jobseek-${service}-backup.service"; then
    echo "ERROR: backup service is failed" >&2
    exit 1
  fi
elif [[ "$service" != "web-postgresql" ]]; then
  echo "ERROR: required backup timer is disabled" >&2
  exit 1
fi

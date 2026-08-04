#!/usr/bin/env bash
# Receive one root-only ATS deployment credential payload on stdin.
set -euo pipefail
umask 077

payload="$(mktemp /run/jobseek-ats-inventory-payload.XXXXXX)"
credential_root=""
cleanup() {
  trap - EXIT HUP INT TERM
  rm -f -- "$payload"
  [[ -z "$credential_root" ]] || rm -rf -- "$credential_root"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cat >"$payload"
chown root:root "$payload"
chmod 0600 "$payload"
payload_size="$(stat -c '%s' "$payload")"
[[ "$payload_size" =~ ^[0-9]+$ && "$payload_size" -gt 0 && "$payload_size" -le 131072 ]] || {
  echo "ERROR: ATS deployment payload exceeds its bound" >&2
  exit 1
}
mapfile -t fields <"$payload"
[[ ${#fields[@]} -eq 4 ]] || { echo "ERROR: invalid ATS deployment payload" >&2; exit 1; }
deploy_sha="${fields[0]}"
[[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]] || exit 2

credential_root="$(mktemp -d /run/jobseek-ats-inventory-credentials.XXXXXX)"
app_id_file="$credential_root/github-app-id"
installation_id_file="$credential_root/github-app-installation-id"
private_key_file="$credential_root/github-app-private-key"
printf '%s' "${fields[1]}" | base64 --decode >"$app_id_file"
printf '%s' "${fields[2]}" | base64 --decode >"$installation_id_file"
printf '%s' "${fields[3]}" | base64 --decode >"$private_key_file"
chown root:root "$app_id_file" "$installation_id_file" "$private_key_file"
chmod 0600 "$app_id_file" "$installation_id_file" "$private_key_file"
unset fields
rm -f -- "$payload"

cd /opt/jobseek-ats-inventory
JOBSEEK_ATS_INVENTORY_DEPLOY_SHA="$deploy_sha" \
JOBSEEK_GITHUB_APP_ID_FILE="$app_id_file" \
JOBSEEK_GITHUB_APP_INSTALLATION_ID_FILE="$installation_id_file" \
JOBSEEK_GITHUB_APP_PRIVATE_KEY_FILE="$private_key_file" \
  bash deploy/ats-inventory/install-host.sh

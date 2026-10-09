#!/usr/bin/env bash
# Explicitly dispatched, revision-bound installer. Credential arrives only on stdin.
set -euo pipefail
umask 077
[[ "$(id -u)" = 0 ]]
revision="${1:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec 9>/run/lock/jobseek-ai-filter-refresh.lock
flock -w 120 9
stage="$(mktemp -d /run/jobseek-ai-filter-refresh-install.XXXXXX)"
trap 'rm -rf -- "$stage"' EXIT
IFS= read -r bearer
[[ ${#bearer} -ge 1 && ${#bearer} -le 1024 && "$bearer" != *[!\!-\~]* ]]
printf '%s\n' "$bearer" > "$stage/refresh-bearer"
unset bearer
# An old/absent web endpoint fails before changing an installed timer.
CREDENTIALS_DIRECTORY="$stage" /usr/bin/python3 "$source_root/scripts/ai-filter-refresh.py" >/dev/null
paths=(
  /usr/local/lib/jobseek-ai-filter-refresh/runner.py
  /etc/jobseek-ai-filter-refresh/refresh-bearer
  /etc/systemd/system/jobseek-ai-filter-refresh.service
  /etc/systemd/system/jobseek-ai-filter-refresh.timer
  /etc/jobseek-ai-filter-refresh/revision
)
timer_enabled=0
timer_active=0
systemctl is-enabled --quiet jobseek-ai-filter-refresh.timer && timer_enabled=1 || true
systemctl is-active --quiet jobseek-ai-filter-refresh.timer && timer_active=1 || true
for index in "${!paths[@]}"; do
  if [[ -f "${paths[$index]}" ]]; then cp -p "${paths[$index]}" "$stage/previous-$index"; fi
done
rollback() {
  local status=$?
  trap - ERR
  systemctl stop jobseek-ai-filter-refresh.timer jobseek-ai-filter-refresh.service || true
  for index in "${!paths[@]}"; do
    if [[ -f "$stage/previous-$index" ]]; then cp -p "$stage/previous-$index" "${paths[$index]}"; else rm -f "${paths[$index]}"; fi
  done
  systemctl daemon-reload
  if [[ "$timer_enabled" = 1 ]]; then systemctl enable jobseek-ai-filter-refresh.timer; else systemctl disable jobseek-ai-filter-refresh.timer || true; fi
  if [[ "$timer_active" = 1 ]]; then systemctl start jobseek-ai-filter-refresh.timer; fi
  exit "$status"
}
trap rollback ERR
systemctl stop jobseek-ai-filter-refresh.timer jobseek-ai-filter-refresh.service || true
install -d -o root -g root -m 0755 /usr/local/lib/jobseek-ai-filter-refresh
install -d -o root -g root -m 0700 /etc/jobseek-ai-filter-refresh
install -o root -g root -m 0644 "$source_root/scripts/ai-filter-refresh.py" "${paths[0]}"
install -o root -g root -m 0600 "$stage/refresh-bearer" "${paths[1]}"
install -o root -g root -m 0644 "$source_root/deploy/systemd/jobseek-ai-filter-refresh.service" "${paths[2]}"
install -o root -g root -m 0644 "$source_root/deploy/systemd/jobseek-ai-filter-refresh.timer" "${paths[3]}"
systemctl daemon-reload
systemctl start jobseek-ai-filter-refresh.service
systemctl enable --now jobseek-ai-filter-refresh.timer
systemctl is-active --quiet jobseek-ai-filter-refresh.timer
printf '%s\n' "$revision" > "${paths[4]}"
trap - ERR
printf '%s\n' 'ai_filter_refresh_timer_installed'

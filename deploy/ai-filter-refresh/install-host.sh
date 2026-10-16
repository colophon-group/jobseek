#!/usr/bin/env bash
# Revision-bound installer. The restricted worker env-file arrives on stdin.
set -euo pipefail
umask 077
[[ "$(id -u)" = 0 ]]
revision="${1:-}"
manifest_hash="${2:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$manifest_hash" =~ ^[0-9a-f]{64}$ ]]
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec 9>/run/lock/jobseek-ai-filter-refresh.lock
flock -w 120 9
stage="$(mktemp -d /run/jobseek-ai-filter-refresh-install.XXXXXX)"
trap 'rm -rf -- "$stage"' EXIT
head -c 65537 > "$stage/worker-env"
python3 - "$stage/worker-env" <<'PY'
import json,sys
from pathlib import Path
raw=Path(sys.argv[1]).read_text()
assert len(raw.encode()) <= 65536 and raw.count('\n') == 1 and raw.endswith('\n')
assert raw.startswith('JOBSEEK_NARROWED_CONFIG_JSON=')
config=json.loads(raw.split('=',1)[1])
assert isinstance(config,dict) and all(isinstance(k,str) and isinstance(v,str) for k,v in config.items())
PY
python3 "$source_root/deploy/ai-filter-refresh/verify-bundle.py" "$source_root/deploy/ai-filter-refresh/bundle" "$revision" "$manifest_hash"
release="/opt/jobseek-narrowed/releases/$revision-${manifest_hash:0:12}"
install -d -o root -g root -m 0755 "$release"
if [[ -f "$release/manifest.json" ]]; then
  python3 "$source_root/deploy/ai-filter-refresh/verify-bundle.py" "$release" "$revision" "$manifest_hash"
else
  install -o root -g root -m 0644 "$source_root/deploy/ai-filter-refresh/bundle/worker.mjs" "$release/worker.mjs"
  install -o root -g root -m 0644 "$source_root/deploy/ai-filter-refresh/bundle/manifest.json" "$release/manifest.json"
fi
image="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["runtimeImage"])' "$release/manifest.json")"
docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image" >/dev/null
# Read-only contract/credential check before stopping the existing worker.
CREDENTIALS_DIRECTORY="$stage" JOBSEEK_NARROWED_BUNDLE="$release" \
  bash "$source_root/deploy/ai-filter-refresh/run-host.sh" --check
paths=(
  /usr/local/lib/jobseek-ai-filter-refresh/run-host.sh
  /usr/local/lib/jobseek-ai-filter-refresh/current
  /etc/jobseek-ai-filter-refresh/worker.env
  /etc/systemd/system/jobseek-ai-filter-refresh.service
  /etc/systemd/system/jobseek-ai-filter-refresh.timer
  /etc/jobseek-ai-filter-refresh/revision
)
timer_enabled=0; timer_active=0; service_enabled=0; service_active=0
systemctl is-enabled --quiet jobseek-ai-filter-refresh.timer && timer_enabled=1 || true
systemctl is-active --quiet jobseek-ai-filter-refresh.timer && timer_active=1 || true
systemctl is-enabled --quiet jobseek-ai-filter-refresh.service && service_enabled=1 || true
systemctl is-active --quiet jobseek-ai-filter-refresh.service && service_active=1 || true
for index in "${!paths[@]}"; do
  if [[ -e "${paths[$index]}" || -L "${paths[$index]}" ]]; then cp -a "${paths[$index]}" "$stage/previous-$index"; fi
done
rollback() {
  local status=$?
  trap - ERR
  systemctl stop jobseek-ai-filter-refresh.timer jobseek-ai-filter-refresh.service || true
  docker rm -f jobseek-narrowed-worker >/dev/null 2>&1 || true
  for index in "${!paths[@]}"; do
    rm -f -- "${paths[$index]}"
    if [[ -e "$stage/previous-$index" || -L "$stage/previous-$index" ]]; then cp -a "$stage/previous-$index" "${paths[$index]}"; fi
  done
  systemctl daemon-reload
  if [[ "$timer_enabled" = 1 ]]; then systemctl enable jobseek-ai-filter-refresh.timer; else systemctl disable jobseek-ai-filter-refresh.timer || true; fi
  if [[ "$timer_active" = 1 ]]; then systemctl start jobseek-ai-filter-refresh.timer; fi
  if [[ "$service_enabled" = 1 ]]; then systemctl enable jobseek-ai-filter-refresh.service; else systemctl disable jobseek-ai-filter-refresh.service || true; fi
  if [[ "$service_active" = 1 ]]; then systemctl start jobseek-ai-filter-refresh.service; fi
  exit "$status"
}
trap rollback ERR
for unit in jobseek-ai-filter-refresh.timer jobseek-ai-filter-refresh.service; do
  if [[ "$(systemctl show "$unit" -p LoadState --value)" = loaded ]]; then systemctl stop "$unit"; fi
done
install -d -o root -g root -m 0755 /usr/local/lib/jobseek-ai-filter-refresh
install -d -o root -g root -m 0700 /etc/jobseek-ai-filter-refresh
install -o root -g root -m 0644 "$source_root/deploy/ai-filter-refresh/run-host.sh" "${paths[0]}"
rm -f -- "${paths[1]}.next"
ln -s "$release" "${paths[1]}.next"
mv -Tf "${paths[1]}.next" "${paths[1]}"
install -o root -g root -m 0600 "$stage/worker-env" "${paths[2]}"
install -o root -g root -m 0644 "$source_root/deploy/systemd/jobseek-ai-filter-refresh.service" "${paths[3]}"
install -o root -g root -m 0644 "$source_root/deploy/systemd/jobseek-ai-filter-refresh.timer" "${paths[4]}"
systemctl daemon-reload
systemctl disable jobseek-ai-filter-refresh.timer
systemctl enable --now jobseek-ai-filter-refresh.service
healthy=0
for attempt in {1..60}; do
  if [[ "$(docker inspect --format '{{.State.Health.Status}}' jobseek-narrowed-worker 2>/dev/null || true)" = healthy ]]; then healthy=1; break; fi
  if systemctl is-failed --quiet jobseek-ai-filter-refresh.service; then break; fi
  sleep 2
done
[[ "$healthy" = 1 ]]
printf '%s\n' "$revision" > "${paths[5]}"
trap - ERR
printf '%s\n' 'narrowed_hetzner_worker_installed'

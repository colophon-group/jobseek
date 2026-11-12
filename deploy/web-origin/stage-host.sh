#!/usr/bin/env bash
set -euo pipefail
umask 077

revision="${1:-}"
archive="${2:-}"
expected_hash="${3:-}"
configuration="${4:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$expected_hash" =~ ^[0-9a-f]{64}$ ]]
[[ -f "$archive" && -f "$configuration" ]]
[[ "$(sha256sum "$archive" | cut -d' ' -f1)" = "$expected_hash" ]]

if ! getent passwd jobseek-web >/dev/null; then
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin jobseek-web
fi
install -d -o root -g root -m 0755 /opt/jobseek-web /opt/jobseek-web/releases
install -d -o root -g jobseek-web -m 0750 /etc/jobseek-web
install -d -o jobseek-web -g jobseek-web -m 0750 /var/cache/jobseek-web

release="/opt/jobseek-web/releases/$revision"
if [[ ! -d "$release" ]]; then
  temporary="$(mktemp -d /opt/jobseek-web/releases/.stage.XXXXXXXX)"
  trap 'rm -rf -- "$temporary"' EXIT
  tar -xzf "$archive" -C "$temporary" --no-same-owner --no-same-permissions
  test -s "$temporary/apps/web/server.js"
  install -d -o jobseek-web -g jobseek-web -m 0750 "/var/cache/jobseek-web/$revision"
  test ! -e "$temporary/apps/web/.next/cache"
  ln -s "/var/cache/jobseek-web/$revision" "$temporary/apps/web/.next/cache"
  chown -R root:jobseek-web "$temporary"
  chmod -R u+rwX,g+rX,g-w,o-rwx "$temporary"
  mv -- "$temporary" "$release"
  trap - EXIT
fi
test -s "$release/apps/web/server.js"

node - "$configuration" "$revision" > "/etc/jobseek-web/runtime-$revision.json.tmp" <<'JS'
const fs = require('node:fs');
const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (!config || typeof config !== 'object' || !config.env || typeof config.env !== 'object' || Array.isArray(config.env)) throw new Error('invalid runtime environment');
for (const [key, value] of Object.entries(config.env)) {
  if (!/^[A-Z][A-Z0-9_]*$/.test(key) || typeof value !== 'string' || value.includes('\0')) throw new Error('invalid runtime environment');
}
for (const key of ['VERCEL_TOKEN','CLOUDFLARE_API_TOKEN','HETZNER_API_TOKEN','DATABASE_URL_UNPOOLED']) {
  if (key in config.env) throw new Error('provider or migration credential in web runtime');
}
for (const key of ['DATABASE_URL','BETTER_AUTH_SECRET','BETTER_AUTH_URL','NEXT_SERVER_ACTIONS_ENCRYPTION_KEY']) {
  if (!config.env[key]) throw new Error(`missing required web runtime key: ${key}`);
}
console.log(JSON.stringify({release:`/opt/jobseek-web/releases/${process.argv[3]}`,env:config.env}));
JS
install -o root -g jobseek-web -m 0640 "/etc/jobseek-web/runtime-$revision.json.tmp" "/etc/jobseek-web/runtime-$revision.json"
rm -f -- "/etc/jobseek-web/runtime-$revision.json.tmp"
install -d -o root -g root -m 0755 /usr/local/lib/jobseek-web
install -o root -g root -m 0644 "$(dirname "$0")/launch.mjs" /usr/local/lib/jobseek-web/launch.mjs
install -o root -g root -m 0644 "$(dirname "$0")/run-alerts.mjs" /usr/local/lib/jobseek-web/run-alerts.mjs
install -o root -g root -m 0644 "$(dirname "$0")/jobseek-web@.service" /etc/systemd/system/jobseek-web@.service
install -o root -g root -m 0644 "$(dirname "$0")/jobseek-web-alerts.service" /etc/systemd/system/jobseek-web-alerts.service
install -o root -g root -m 0644 "$(dirname "$0")/jobseek-web-alerts.timer" /etc/systemd/system/jobseek-web-alerts.timer
systemd-analyze verify /etc/systemd/system/jobseek-web@.service /etc/systemd/system/jobseek-web-alerts.service /etc/systemd/system/jobseek-web-alerts.timer
systemctl daemon-reload
install -o root -g jobseek-web -m 0640 "/etc/jobseek-web/runtime-$revision.json" /etc/jobseek-web/candidate.json
systemctl restart jobseek-web@candidate.service

healthy=0
for _ in {1..30}; do
  if curl --silent --show-error --max-time 5 --output /dev/null \
    --header 'Host: jseek.co' --header 'X-Forwarded-Proto: https' --fail http://127.0.0.1:3001/en/sign-in; then
    healthy=1
    break
  fi
  if systemctl is-failed --quiet jobseek-web@candidate.service; then break; fi
  sleep 2
done
if [[ "$healthy" != 1 ]]; then
  systemctl stop jobseek-web@candidate.service || true
  echo 'web_origin_candidate_unhealthy' >&2
  exit 1
fi
printf '%s\n' "web_origin_candidate_healthy revision=$revision"

#!/usr/bin/env bash
set -euo pipefail
umask 077

token_source="${1:-}"
test -s "$token_source"
systemctl is-active --quiet jobseek-web@production.service
curl --silent --show-error --max-time 5 --output /dev/null \
  --header 'Host: jseek.co' --header 'X-Forwarded-Proto: https' --fail http://127.0.0.1:3000/en/sign-in

version=2026.10.7
expected_hash=1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872
binary=/usr/local/lib/jobseek-web/cloudflared
install -d -o root -g root -m 0755 /usr/local/lib/jobseek-web
if [[ ! -x "$binary" || \
      "$(sha256sum "$binary" | cut -d' ' -f1)" != "$expected_hash" ]]; then
  temporary="$(mktemp /run/jobseek-cloudflared.XXXXXXXX)"
  trap 'rm -f -- "$temporary"' EXIT
  curl --fail --location --silent --show-error --max-time 120 \
    "https://github.com/cloudflare/cloudflared/releases/download/$version/cloudflared-linux-amd64" \
    --output "$temporary"
  test "$(sha256sum "$temporary" | cut -d' ' -f1)" = "$expected_hash"
  install -o root -g root -m 0755 "$temporary" "$binary"
  rm -f -- "$temporary"
  trap - EXIT
fi

if ! getent passwd cloudflared >/dev/null; then
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin cloudflared
fi
install -o root -g root -m 0600 "$token_source" /etc/jobseek-web/cloudflare-tunnel-token
install -o root -g root -m 0644 "$(dirname "$0")/jobseek-web-tunnel.service" /etc/systemd/system/jobseek-web-tunnel.service
systemd-analyze verify /etc/systemd/system/jobseek-web-tunnel.service
systemctl daemon-reload
systemctl enable --now jobseek-web-tunnel.service
systemctl is-active --quiet jobseek-web-tunnel.service
printf '%s\n' 'web_origin_tunnel_active'

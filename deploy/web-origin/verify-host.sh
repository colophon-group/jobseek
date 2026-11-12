#!/usr/bin/env bash
set -euo pipefail

revision="${1:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
test "$(node -p 'JSON.parse(require("node:fs").readFileSync("/etc/jobseek-web/production.json", "utf8")).release')" = "/opt/jobseek-web/releases/$revision"
systemctl is-active --quiet jobseek-web@production.service
systemctl is-active --quiet jobseek-web-tunnel.service

#!/usr/bin/env bash
set -euo pipefail
umask 077

revision="${1:-}"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
test "$(node -p 'JSON.parse(require("node:fs").readFileSync("/etc/jobseek-web/candidate.json", "utf8")).release')" = "/opt/jobseek-web/releases/$revision"
systemctl is-active --quiet jobseek-web@candidate.service
curl --silent --show-error --max-time 5 --output /dev/null \
  --header 'Host: jseek.co' --header 'X-Forwarded-Proto: https' --fail http://127.0.0.1:3001/en/sign-in

previous="$(mktemp /run/jobseek-web-previous.XXXXXXXX)"
had_previous=0
if [[ -f /etc/jobseek-web/production.json ]]; then
  cp -a /etc/jobseek-web/production.json "$previous"
  had_previous=1
fi
rollback() {
  local status=$?
  trap - ERR
  if [[ "$had_previous" = 1 ]]; then
    cp -a "$previous" /etc/jobseek-web/production.json
    systemctl restart jobseek-web@production.service || true
  else
    systemctl stop jobseek-web@production.service || true
    rm -f -- /etc/jobseek-web/production.json
  fi
  rm -f -- "$previous"
  exit "$status"
}
trap rollback ERR
install -o root -g jobseek-web -m 0640 /etc/jobseek-web/candidate.json /etc/jobseek-web/production.json
systemctl enable jobseek-web@production.service
systemctl restart jobseek-web@production.service
healthy=0
for _ in {1..30}; do
  if curl --silent --show-error --max-time 5 --output /dev/null \
    --header 'Host: jseek.co' --header 'X-Forwarded-Proto: https' --fail http://127.0.0.1:3000/en/sign-in; then
    healthy=1
    break
  fi
  if systemctl is-failed --quiet jobseek-web@production.service; then break; fi
  sleep 2
done
[[ "$healthy" = 1 ]]
alerts_mode="$(node -p 'JSON.parse(require("node:fs").readFileSync("/etc/jobseek-web/production.json", "utf8")).env.JOB_ALERTS_MODE')"
if [[ "$alerts_mode" = off ]]; then
  systemctl disable --now jobseek-web-alerts.timer || true
else
  systemctl enable --now jobseek-web-alerts.timer
fi
systemctl stop jobseek-web@candidate.service
trap - ERR
rm -f -- "$previous"
printf '%s\n' "web_origin_active revision=$revision"

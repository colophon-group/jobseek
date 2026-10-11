#!/usr/bin/env bash
set -euo pipefail
test "$(systemctl show jobseek-ai-filter-refresh.timer -p LoadState --value)" = loaded
systemctl disable --now jobseek-ai-filter-refresh.timer
systemctl stop jobseek-ai-filter-refresh.service
# Stopped units can be garbage-collected. Reset only a retained failure.
if systemctl is-failed --quiet jobseek-ai-filter-refresh.service; then
  systemctl reset-failed jobseek-ai-filter-refresh.service
fi
test "$(systemctl is-enabled jobseek-ai-filter-refresh.timer || true)" = disabled
test "$(systemctl is-active jobseek-ai-filter-refresh.timer || true)" = inactive
test "$(systemctl is-active jobseek-ai-filter-refresh.service || true)" = inactive
printf '%s\n' ai_filter_refresh_paused

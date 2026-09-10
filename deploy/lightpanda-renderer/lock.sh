#!/usr/bin/env bash
# Atomically create and acquire the deploy-owned renderer transaction lock.

acquire_renderer_lock() {
  local lock_path="${1:?lock path is required}"
  local wait_seconds="${2:-900}"
  local fd_identity path_identity

  if [[ ! -e "$lock_path" && ! -L "$lock_path" ]]; then
    (umask 077; set -o noclobber; : >"$lock_path") 2>/dev/null || :
  fi
  [[ -f "$lock_path" && ! -L "$lock_path" ]] || return 1
  [[ "$(stat -c '%U:%G:%a' "$lock_path")" == deploy:deploy:600 ]] || return 1

  exec 9<>"$lock_path"
  fd_identity="$(stat -Lc '%d:%i' "/proc/$$/fd/9")"
  path_identity="$(stat -Lc '%d:%i' "$lock_path")"
  [[ "$fd_identity" == "$path_identity" ]] || return 1
  flock -w "$wait_seconds" 9 || return 1
  [[ "$(stat -Lc '%d:%i' "$lock_path")" == "$fd_identity" ]] || return 1
}

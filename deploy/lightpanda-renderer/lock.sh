#!/usr/bin/env bash
# Atomically create and acquire the deploy-owned renderer lifecycle lock.

acquire_renderer_lock() {
  local lock_path="${1:?lock path is required}"
  local wait_seconds="${2:-900}"
  local fd_identity path_identity

  if [[ ! -e "$lock_path" && ! -L "$lock_path" ]]; then
    # Bash redirection requests mode 0666. A parent default ACL can therefore
    # grant group/other bits even under umask 077. Publish the final path once,
    # with an ACL-safe 0600 mode; only a competing exclusive creator is benign.
    python3 - "$lock_path" <<'PY' || return 1
import os
import sys

flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
try:
    fd = os.open(sys.argv[1], flags, 0o600)
except FileExistsError:
    raise SystemExit(0)
except OSError:
    raise SystemExit(1)
try:
    os.fchmod(fd, 0o600)
finally:
    os.close(fd)
PY
  fi
  [[ -f "$lock_path" && ! -L "$lock_path" ]] || return 1
  [[ "$(stat -c '%U:%G:%a' "$lock_path")" == deploy:deploy:600 ]] || return 1

  exec 9<>"$lock_path" || return 1
  fd_identity="$(stat -Lc '%d:%i' "/proc/$$/fd/9")" || return 1
  path_identity="$(stat -Lc '%d:%i' "$lock_path")" || return 1
  [[ "$fd_identity" == "$path_identity" ]] || return 1
  flock -w "$wait_seconds" 9 || return 1
  [[ "$(stat -Lc '%d:%i' "$lock_path")" == "$fd_identity" ]] || return 1
}

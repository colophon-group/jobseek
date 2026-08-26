#!/usr/bin/env bash
# Refuse the 6 GiB Typesense policy on hosts without protected OS headroom.
set -euo pipefail

MINIMUM_HOST_MEMORY_BYTES=7516192768
capacity_test_root=""

cleanup_self_test() {
  if [[ -n "$capacity_test_root" ]]; then
    rm -rf -- "$capacity_test_root"
  fi
}

check_memory_capacity() {
  local meminfo_path=${1:-/proc/meminfo}
  local line memtotal_count memory_kib memory_bytes

  if [[ ! -r "$meminfo_path" ]]; then
    echo "ERROR: Typesense host memory source is not readable: $meminfo_path" >&2
    return 1
  fi

  memtotal_count=$(awk '$1 == "MemTotal:" { count++ } END { print count + 0 }' "$meminfo_path")
  if [[ "$memtotal_count" -ne 1 ]]; then
    echo "ERROR: Typesense host memory source must contain exactly one MemTotal entry" >&2
    return 1
  fi
  line=$(awk '$1 == "MemTotal:" { print }' "$meminfo_path")
  if [[ ! "$line" =~ ^MemTotal:[[:space:]]+([0-9]+)[[:space:]]+kB$ ]]; then
    echo "ERROR: Typesense host MemTotal entry is malformed" >&2
    return 1
  fi
  memory_kib=${BASH_REMATCH[1]}
  if [[ "$memory_kib" -le 0 ]]; then
    echo "ERROR: Typesense host MemTotal must be positive" >&2
    return 1
  fi
  memory_bytes=$((memory_kib * 1024))
  if [[ "$memory_bytes" -lt "$MINIMUM_HOST_MEMORY_BYTES" ]]; then
    echo "ERROR: Typesense 6 GiB policy requires at least 7 GiB host memory; found ${memory_bytes} bytes" >&2
    return 1
  fi

  echo "Typesense host memory preflight passed: total=${memory_bytes} minimum=${MINIMUM_HOST_MEMORY_BYTES}"
}

self_test() {
  local root
  root=$(mktemp -d)
  capacity_test_root=$root
  trap cleanup_self_test EXIT

  printf 'MemTotal:       8388608 kB\n' >"$root/eight-gib"
  check_memory_capacity "$root/eight-gib" >/dev/null

  printf 'MemTotal:       7340032 kB\n' >"$root/seven-gib"
  check_memory_capacity "$root/seven-gib" >/dev/null

  printf 'MemTotal:       4194304 kB\n' >"$root/four-gib"
  if check_memory_capacity "$root/four-gib" >/dev/null 2>&1; then
    echo "ERROR: 4 GiB self-test fixture unexpectedly passed" >&2
    return 1
  fi

  printf 'MemTotal: unknown kB\n' >"$root/malformed"
  if check_memory_capacity "$root/malformed" >/dev/null 2>&1; then
    echo "ERROR: malformed self-test fixture unexpectedly passed" >&2
    return 1
  fi

  printf 'MemTotal: 8388608 kB\nMemTotal: 8388608 kB\n' >"$root/duplicate"
  if check_memory_capacity "$root/duplicate" >/dev/null 2>&1; then
    echo "ERROR: duplicate MemTotal self-test fixture unexpectedly passed" >&2
    return 1
  fi

  echo "Typesense host memory preflight self-test passed"
}

case "${1:-}" in
  --self-test)
    [[ "$#" -eq 1 ]]
    self_test
    ;;
  --help|-h)
    echo "Usage: $0 [--self-test|MEMINFO_PATH]"
    ;;
  *)
    [[ "$#" -le 1 ]]
    check_memory_capacity "${1:-/proc/meminfo}"
    ;;
esac

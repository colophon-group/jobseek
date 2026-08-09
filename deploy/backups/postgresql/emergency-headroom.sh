#!/usr/bin/env bash
# Maintain a root-owned, allocated recovery reserve on the PostgreSQL XFS Volume.
set -euo pipefail

ACTION="${1:-status}"
TARGET_BYTES="${JOBSEEK_POSTGRES_RESERVE_BYTES:-2147483648}"
MIN_FREE_AFTER_BYTES="${JOBSEEK_POSTGRES_MIN_FREE_AFTER_RESERVE_BYTES:-8589934592}"
DATA_MOUNT="${JOBSEEK_POSTGRES_DATA_MOUNT:-}"

for value in "$TARGET_BYTES" "$MIN_FREE_AFTER_BYTES"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || {
    echo "ERROR: reserve thresholds must be positive integers" >&2
    exit 2
  }
done

if [[ -z "$DATA_MOUNT" ]]; then
  data_source="$(docker inspect postgres --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}')"
  [[ -n "$data_source" ]] || {
    echo "ERROR: PostgreSQL data bind mount was not found" >&2
    exit 1
  }
  mapfile -t data_mounts < <(
    findmnt -n -o TARGET --target "$data_source" |
      awk 'NF && !seen[$0]++'
  )
  [[ "${#data_mounts[@]}" -eq 1 ]] || {
    echo "ERROR: PostgreSQL data filesystem mountpoint is ambiguous" >&2
    exit 1
  }
  DATA_MOUNT="${data_mounts[0]}"
fi

[[ -d "$DATA_MOUNT" ]] || {
  echo "ERROR: PostgreSQL data mount is missing" >&2
  exit 1
}
if [[ "${JOBSEEK_POSTGRES_ALLOW_TEST_FS:-0}" != 1 && "$DATA_MOUNT" != /mnt/* ]]; then
  echo "ERROR: refusing an unexpected PostgreSQL data mount" >&2
  exit 1
fi
if [[ "${JOBSEEK_POSTGRES_ALLOW_TEST_FS:-0}" != 1 ]]; then
  mapfile -t data_filesystems < <(
    findmnt -n -o FSTYPE --target "$DATA_MOUNT" |
      awk 'NF && !seen[$0]++'
  )
  [[ "${#data_filesystems[@]}" -eq 1 && "${data_filesystems[0]}" == xfs ]] || {
    echo "ERROR: PostgreSQL emergency reserve requires the attached XFS Volume" >&2
    exit 1
  }
fi

RESERVE_FILE="$DATA_MOUNT/.jobseek-postgresql-emergency-reserve"

allocated_bytes() {
  local blocks block_size
  read -r blocks block_size < <(stat -c '%b %B' -- "$RESERVE_FILE")
  printf '%s\n' "$((blocks * block_size))"
}

validate_existing() {
  [[ ! -L "$RESERVE_FILE" && -f "$RESERVE_FILE" ]] || {
    echo "ERROR: emergency reserve is missing or not a regular file" >&2
    return 1
  }
  if [[ "${JOBSEEK_POSTGRES_ALLOW_TEST_FS:-0}" != 1 ]]; then
    [[ "$(stat -c '%u:%g:%a' -- "$RESERVE_FILE")" == "0:0:600" ]] || {
      echo "ERROR: emergency reserve ownership or mode is unsafe" >&2
      return 1
    }
  fi
  [[ "$(stat -c '%s' -- "$RESERVE_FILE")" -eq "$TARGET_BYTES" ]] || {
    echo "ERROR: emergency reserve logical size is unexpected" >&2
    return 1
  }
  [[ "$(allocated_bytes)" -ge "$TARGET_BYTES" ]] || {
    echo "ERROR: emergency reserve is sparse or incompletely allocated" >&2
    return 1
  }
}

case "$ACTION" in
  status)
    validate_existing
    printf 'reserve_bytes=%s\n' "$(allocated_bytes)"
    ;;
  reserve)
    if [[ -e "$RESERVE_FILE" || -L "$RESERVE_FILE" ]]; then
      validate_existing
      printf 'PostgreSQL emergency reserve already exact: %s bytes\n' "$TARGET_BYTES"
      exit 0
    fi
    available_kib="$(df -Pk "$DATA_MOUNT" | awk 'NR == 2 {print $4}')"
    [[ "$available_kib" =~ ^[0-9]+$ ]] || {
      echo "ERROR: unable to measure PostgreSQL data Volume headroom" >&2
      exit 1
    }
    available="$((available_kib * 1024))"
    required="$((TARGET_BYTES + MIN_FREE_AFTER_BYTES))"
    (( available >= required )) || {
      echo "ERROR: insufficient free space to allocate the recovery reserve safely" >&2
      exit 1
    }
    temporary="$RESERVE_FILE.tmp.$$"
    [[ ! -e "$temporary" && ! -L "$temporary" ]] || {
      echo "ERROR: emergency reserve temporary path already exists" >&2
      exit 1
    }
    cleanup() { rm -f -- "$temporary"; }
    trap cleanup EXIT HUP INT TERM
    fallocate --length "$TARGET_BYTES" "$temporary"
    chmod 0600 "$temporary"
    if [[ "${JOBSEEK_POSTGRES_ALLOW_TEST_FS:-0}" != 1 ]]; then
      chown root:root "$temporary"
    fi
    mv -n "$temporary" "$RESERVE_FILE"
    if [[ -e "$temporary" || -L "$temporary" ]]; then
      rm -f -- "$temporary"
      echo "ERROR: emergency reserve appeared during allocation" >&2
      exit 1
    fi
    trap - EXIT HUP INT TERM
    validate_existing
    printf 'Allocated PostgreSQL emergency reserve: %s bytes\n' "$TARGET_BYTES"
    ;;
  release)
    validate_existing
    rm -- "$RESERVE_FILE"
    printf 'Released PostgreSQL emergency reserve for incident recovery\n'
    ;;
  *)
    echo "Usage: $0 <status|reserve|release>" >&2
    exit 2
    ;;
esac

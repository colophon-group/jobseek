#!/usr/bin/env bash
# Root operator control for the ATS inventory write gate and canary stage.
set -euo pipefail
umask 077

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: control requires root" >&2; exit 1; }
CONFIG_ROOT=/etc/jobseek-ats-inventory
CONFIG="$CONFIG_ROOT/config.env"
DISABLED="$CONFIG_ROOT/writes-disabled"
ACTION="${1:-status}"

write_config() {
  local mode="$1" cap="$2" temporary
  case "$mode" in report|dry-run|refill) ;; *) echo "ERROR: invalid mode" >&2; exit 2 ;; esac
  case "$cap" in 1|5|25) ;; *) echo "ERROR: cap must be 1, 5, or 25" >&2; exit 2 ;; esac
  temporary="$(mktemp "$CONFIG_ROOT/config.env.XXXXXX")"
  printf 'ATS_INVENTORY_MODE=%s\nATS_INVENTORY_ROLLOUT_CAP=%s\n' "$mode" "$cap" >"$temporary"
  chown root:deploy "$temporary"
  chmod 0640 "$temporary"
  mv -f "$temporary" "$CONFIG"
}

case "$ACTION" in
  status)
    [[ -r "$CONFIG" ]] && sed -n '/^ATS_INVENTORY_\(MODE\|ROLLOUT_CAP\)=/p' "$CONFIG"
    if [[ -e "$DISABLED" ]]; then
      echo "ATS_INVENTORY_WRITES=disabled"
    else
      echo "ATS_INVENTORY_WRITES=enabled"
    fi
    ;;
  disable)
    install -o root -g deploy -m 0640 /dev/null "$DISABLED"
    systemctl stop jobseek-ats-inventory.service
    echo "ATS inventory writes disabled; cache and ledger retained"
    ;;
  configure)
    [[ $# -eq 3 ]] || { echo "usage: $0 configure <report|dry-run|refill> <1|5|25>" >&2; exit 2; }
    write_config "$2" "$3"
    echo "ATS inventory configuration updated; write gate unchanged"
    ;;
  enable)
    [[ $# -eq 1 ]] || { echo "usage: $0 enable" >&2; exit 2; }
    [[ -r "$CONFIG" ]] || { echo "ERROR: configure a rollout before enabling" >&2; exit 1; }
    rm -f "$DISABLED"
    echo "ATS inventory write gate enabled"
    ;;
  *)
    echo "usage: $0 <status|disable|configure MODE CAP|enable>" >&2
    exit 2
    ;;
esac

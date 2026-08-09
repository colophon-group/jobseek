#!/usr/bin/env bash
# Stage, verify, commit, or roll back the Jobseek host ingress baseline.
set -euo pipefail

ACTION="${1:-}"
ROLE="${2:-}"
TX_ARG="${3:-}"
CRAWLER_PRIVATE_IP="${JOBSEEK_CRAWLER_PRIVATE_IP:-}"
DEPLOY_SHA="${JOBSEEK_INGRESS_DEPLOY_SHA:-}"
STATE_ROOT=/var/lib/jobseek-ingress
ROLLBACK_ROOT="${STATE_ROOT}/rollback"
PENDING_FILE="${STATE_ROOT}/pending"
SSHD_DROPIN=/etc/ssh/sshd_config.d/00-jobseek-baseline.conf
CONFORMANCE=/usr/local/sbin/jobseek-ingress-conformance
BASELINE=/usr/local/sbin/jobseek-ingress-baseline
ROLLBACK_UNIT=jobseek-ingress-rollback
ROLLBACK_ARMED=0
ACTIVE_TX=""

usage() {
  echo "Usage: $0 <audit|stage|commit|rollback> <crawler|postgresql|typesense> [transaction]" >&2
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

case "$ROLE" in
  crawler|postgresql|typesense) ;;
  *) usage; exit 2 ;;
esac

[[ "$ACTION" =~ ^(audit|stage|commit|rollback)$ ]] || { usage; exit 2; }
[[ "$(id -u)" -eq 0 ]] || fail "must run as root"
[[ "$CRAWLER_PRIVATE_IP" =~ ^10\.|^192\.168\.|^172\.(1[6-9]|2[0-9]|3[01])\. ]] ||
  fail "crawler private IPv4 is required"
[[ "$CRAWLER_PRIVATE_IP" != *$'\n'* && "$CRAWLER_PRIVATE_IP" != *$'\r'* ]] ||
  fail "invalid crawler private IPv4"
if [[ -n "$DEPLOY_SHA" ]]; then
  [[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "invalid deployment SHA"
fi

install_layout() {
  install -d -o root -g root -m 0700 "$STATE_ROOT" "$ROLLBACK_ROOT"
  if [[ "$(readlink -f "${BASH_SOURCE[0]}")" == "$BASELINE" ]]; then
    [[ -x "$CONFORMANCE" ]] || fail "installed conformance probe is missing"
  else
    local repo_root
    repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    install -o root -g root -m 0755 \
      "${repo_root}/scripts/jobseek-ingress-conformance.py" \
      "$CONFORMANCE"
    install -o root -g root -m 0755 "${repo_root}/deploy/networking/install-host.sh" "$BASELINE"
  fi
}

current_tx() {
  [[ -s "$PENDING_FILE" ]] || fail "no pending host ingress transaction"
  local tx
  tx="$(cat "$PENDING_FILE")"
  [[ "$tx" == "$ROLLBACK_ROOT"/* && -d "$tx" ]] || fail "invalid pending transaction"
  printf '%s\n' "$tx"
}

snapshot_previous() {
  local stamp tx ufw_status
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  tx="${ROLLBACK_ROOT}/${stamp}"
  install -d -o root -g root -m 0700 "$tx"
  cp -a /etc/ufw "$tx/ufw"
  cp -a /etc/default/ufw "$tx/default-ufw"
  ufw_status="$(ufw status)"
  # ufw.service is a oneshot unit and may remain "active (exited)" while the
  # firewall itself is disabled. Only UFW's own status reflects whether the
  # pre-transaction policy must be re-enabled during rollback.
  if grep -Fqx 'Status: active' <<<"$ufw_status"; then
    touch "$tx/ufw-was-active"
  fi
  if [[ -f "$SSHD_DROPIN" ]]; then
    cp -a "$SSHD_DROPIN" "$tx/sshd-dropin"
  else
    touch "$tx/sshd-dropin-absent"
  fi
  if [[ "$ROLE" == "postgresql" ]]; then
    local root_status
    root_status="$(passwd -S root)"
    [[ "$root_status" == root\ P\ * || "$root_status" == root\ L\ * || "$root_status" == root\ LK\ * ]] ||
      fail "could not determine the root password state"
    if [[ "$root_status" == root\ P\ * ]]; then
      touch "$tx/root-password-was-unlocked"
    fi
  fi
  printf '%s\n' "$tx" >"${PENDING_FILE}.tmp"
  chmod 0600 "${PENDING_FILE}.tmp"
  mv "${PENDING_FILE}.tmp" "$PENDING_FILE"
  printf '%s\n' "$tx"
}

cancel_rollback_timer() {
  # Never stop the service here: automatic rollback executes inside that
  # service, and stopping itself would terminate the restore mid-transaction.
  systemctl stop "${ROLLBACK_UNIT}.timer" >/dev/null 2>&1 || true
  systemctl reset-failed "${ROLLBACK_UNIT}.service" >/dev/null 2>&1 || true
}

restore_previous() {
  local tx="$1"
  [[ "$tx" == "$ROLLBACK_ROOT"/* && -d "$tx/ufw" ]] || fail "invalid rollback transaction"
  cancel_rollback_timer
  # Flush the live staged policy while its ENABLED=yes state is still on
  # disk. Replacing /etc/ufw first can make `ufw disable` treat an active
  # kernel policy as already disabled and leave the host unreachable.
  ufw --force disable >/dev/null
  rm -rf /etc/ufw
  cp -a "$tx/ufw" /etc/ufw
  cp -a "$tx/default-ufw" /etc/default/ufw
  if [[ -f "$tx/sshd-dropin" ]]; then
    install -o root -g root -m 0644 "$tx/sshd-dropin" "$SSHD_DROPIN"
  else
    rm -f "$SSHD_DROPIN"
  fi
  sshd -t
  systemctl reload ssh.service
  if [[ -f "$tx/ufw-was-active" ]]; then
    ufw --force enable >/dev/null
    ufw reload >/dev/null
  else
    ufw --force disable >/dev/null
  fi
  if [[ "$ROLE" == "postgresql" && -f "$tx/root-password-was-unlocked" ]]; then
    passwd -u root >/dev/null
  fi
  rm -f "$PENDING_FILE"
  echo "Restored the previous host ingress policy for role=${ROLE}"
}

rollback_on_error() {
  local status=$?
  trap - ERR EXIT
  if [[ "$ROLLBACK_ARMED" == "1" && -n "$ACTIVE_TX" ]]; then
    restore_previous "$ACTIVE_TX" || true
  fi
  exit "$status"
}

write_sshd_baseline() {
  local allow_users=root
  if id -u deploy >/dev/null 2>&1 && [[ -s /home/deploy/.ssh/authorized_keys ]]; then
    allow_users='root deploy'
  fi
  [[ -s /root/.ssh/authorized_keys ]] || fail "root authorized_keys is required before password lockout"
  install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d
  local temporary
  temporary="$(mktemp /etc/ssh/sshd_config.d/.00-jobseek-baseline.XXXXXX)"
  cat >"$temporary" <<EOF
# Managed by colophon-group/jobseek deploy/networking/install-host.sh.
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
AllowUsers ${allow_users}
DisableForwarding yes
X11Forwarding no
PermitTunnel no
GatewayPorts no
PermitUserEnvironment no
EOF
  chmod 0644 "$temporary"
  chown root:root "$temporary"
  mv "$temporary" "$SSHD_DROPIN"
  sshd -t
}

write_ufw_baseline() {
  sed -i 's/^IPV6=.*/IPV6=yes/' /etc/default/ufw
  ufw --force reset >/dev/null
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow 22/tcp comment 'jobseek:ssh' >/dev/null
  if [[ "$ROLE" == "postgresql" ]]; then
    ufw allow from "$CRAWLER_PRIVATE_IP" to any port 5432 proto tcp \
      comment 'jobseek:crawler-postgresql' >/dev/null
  elif [[ "$ROLE" == "typesense" ]]; then
    ufw allow from "$CRAWLER_PRIVATE_IP" to any port 8108 proto tcp \
      comment 'jobseek:crawler-typesense' >/dev/null
  fi
  ufw --force enable >/dev/null
}

arm_rollback_timer() {
  local tx="$1"
  cancel_rollback_timer
  systemd-run \
    --unit="$ROLLBACK_UNIT" \
    --on-active=15m \
    --property=Type=oneshot \
    --setenv="JOBSEEK_CRAWLER_PRIVATE_IP=${CRAWLER_PRIVATE_IP}" \
    /usr/local/sbin/jobseek-ingress-baseline rollback "$ROLE" "$tx" >/dev/null
}

stage() {
  [[ ! -e "$PENDING_FILE" ]] || fail "a host ingress transaction is already pending"
  local tx
  tx="$(snapshot_previous)"
  ACTIVE_TX="$tx"
  ROLLBACK_ARMED=1
  trap rollback_on_error ERR EXIT
  arm_rollback_timer "$tx"
  write_sshd_baseline
  write_ufw_baseline
  systemctl reload ssh.service
  if [[ "$ROLE" == "postgresql" ]]; then
    passwd -l root >/dev/null
  fi
  "$CONFORMANCE" \
    --role "$ROLE" \
    --crawler-private-ip "$CRAWLER_PRIVATE_IP" \
    --host-only \
    --require-enforced
  ROLLBACK_ARMED=0
  trap - ERR EXIT
  echo "Staged host ingress policy for role=${ROLE}; automatic rollback remains armed"
}

commit() {
  local tx
  tx="$(current_tx)"
  "$CONFORMANCE" \
    --role "$ROLE" \
    --crawler-private-ip "$CRAWLER_PRIVATE_IP" \
    --require-enforced
  cancel_rollback_timer
  if [[ -n "$DEPLOY_SHA" ]]; then
    printf '%s\n' "$DEPLOY_SHA" >"${STATE_ROOT}/deployed-sha.tmp"
    chmod 0644 "${STATE_ROOT}/deployed-sha.tmp"
    mv "${STATE_ROOT}/deployed-sha.tmp" "${STATE_ROOT}/deployed-sha"
  fi
  rm -f "$PENDING_FILE"
  find "$ROLLBACK_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf -- {} +
  echo "Committed host ingress policy for role=${ROLE}"
}

audit() {
  "$CONFORMANCE" --role "$ROLE" --crawler-private-ip "$CRAWLER_PRIVATE_IP"
}

install_layout
case "$ACTION" in
  audit) audit ;;
  stage) stage ;;
  commit) commit ;;
  rollback)
    if [[ -n "$TX_ARG" ]]; then
      restore_previous "$TX_ARG"
    else
      restore_previous "$(current_tx)"
    fi
    ;;
esac

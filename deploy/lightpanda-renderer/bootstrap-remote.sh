#!/usr/bin/env bash
# Transport and run the one-time root host bootstrap over pinned SSH.
set -euo pipefail
set +x
umask 077

trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

: "${TARGET_HOST:?Murmur host is required}"
: "${SSH_PRIVATE_KEY:?SSH private key is required}"
: "${SSH_KNOWN_HOSTS:?pinned Murmur host keys are required}"
: "${RUNNER_TEMP:?runner temporary directory is required}"

SOURCE_COMMIT="${1:-}"
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$TARGET_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || exit 2
[[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ ]] || exit 2
[[ "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ ]] || exit 2

owner="r${GITHUB_RUN_ID}a${GITHUB_RUN_ATTEMPT}"
work_root="$(mktemp -d "$RUNNER_TEMP/jobseek-lightpanda-bootstrap.XXXXXX")"
ssh_root="$work_root/ssh"
payload_root="$work_root/payload"
payload_archive="$work_root/payload.tar.gz"
remote_stage=""

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [[ -n "$remote_stage" && "$remote_stage" =~ ^/tmp/jobseek-lightpanda-bootstrap\.${owner}\.[A-Za-z0-9]+$ ]]; then
    # shellcheck disable=SC2029 # remote_stage is strictly validated above.
    ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
      "rm -rf -- '$remote_stage'" >/dev/null 2>&1 || :
  fi
  rm -rf -- "$work_root"
  exit "$status"
}
trap cleanup EXIT

install -d -m 0700 "$ssh_root" "$payload_root"
printf '%s\n' "$SSH_PRIVATE_KEY" >"$ssh_root/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" >"$ssh_root/known_hosts"
chmod 0600 "$ssh_root/id" "$ssh_root/known_hosts"

artifact_root=deploy/lightpanda-renderer
for artifact in \
  bootstrap-host.sh \
  network-policy.py \
  inventory.json \
  verify.py \
  jobseek-lightpanda-network.service \
  jobseek-lightpanda-network.sudoers; do
  install -m 0600 "$artifact_root/$artifact" "$payload_root/$artifact"
done
tar --create --gzip --file "$payload_archive" --directory "$payload_root" .
payload_size="$(stat -c '%s' "$payload_archive")"
[[ "$payload_size" =~ ^[0-9]+$ && "$payload_size" -le 262144 ]] || exit 1

ssh-keygen -y -f "$ssh_root/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$ssh_root/known_hosts" >/dev/null
ssh_options=(
  -F /dev/null
  -i "$ssh_root/id"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$ssh_root/known_hosts"
  -o GlobalKnownHostsFile=/dev/null
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o ConnectTimeout=15
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=2
  -o LogLevel=ERROR
)

# shellcheck disable=SC2029 # owner is derived from digit-only run identifiers.
remote_stage="$(ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
  "umask 077; mktemp -d '/tmp/jobseek-lightpanda-bootstrap.${owner}.XXXXXX'")"
[[ "$remote_stage" =~ ^/tmp/jobseek-lightpanda-bootstrap\.${owner}\.[A-Za-z0-9]+$ ]] || exit 1

timeout --foreground --signal=TERM --kill-after=30s 5m \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
    "umask 077; tar --extract --gzip --file - --directory '$remote_stage' --no-same-owner --no-same-permissions" \
    <"$payload_archive"
timeout --foreground --signal=TERM --kill-after=30s 10m \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
    "bash '$remote_stage/bootstrap-host.sh' '$remote_stage' '$SOURCE_COMMIT'"

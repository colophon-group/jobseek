#!/usr/bin/env bash
# Run host-hygiene audit/apply/cleanup over host-key-pinned native OpenSSH.
set -euo pipefail
umask 077

MODE="${1:-}"
ROLE="${2:-}"
case "$MODE" in
  audit|apply|cleanup) ;;
  *) exit 2 ;;
esac
case "$ROLE" in
  crawler|postgresql|typesense) ;;
  *) exit 2 ;;
esac

: "${TARGET_HOST:?TARGET_HOST is required}"
: "${SSH_PRIVATE_KEY:?SSH_PRIVATE_KEY is required}"
: "${SSH_KNOWN_HOSTS:?role-specific SSH_KNOWN_HOSTS is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.-]+$ ]] || exit 2

ssh_root="$(mktemp -d "$RUNNER_TEMP/jobseek-host-hygiene-ssh.XXXXXX")"
cleanup_local() { rm -rf -- "$ssh_root"; }
trap cleanup_local EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" >"$ssh_root/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" >"$ssh_root/known_hosts"
chmod 0600 "$ssh_root/id" "$ssh_root/known_hosts"
ssh-keygen -y -f "$ssh_root/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$ssh_root/known_hosts" >"$ssh_root/host-keys"
[[ -s "$ssh_root/host-keys" ]]

ssh_options=(
  -F /dev/null
  -i "$ssh_root/id"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=20
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$ssh_root/known_hosts"
  -o GlobalKnownHostsFile=/dev/null
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -o LogLevel=ERROR
)

remote() {
  local duration="$1"
  shift
  timeout --foreground --signal=TERM --kill-after=30s "$duration" \
    ssh "${ssh_options[@]}" "root@$TARGET_HOST" "$@"
}

if [[ "$MODE" == audit ]]; then
  [[ $# -eq 2 ]]
  # ROLE is allowlisted above; the uploaded program is read from the reviewed checkout.
  # shellcheck disable=SC2029
  remote 5m "python3 - audit --role '$ROLE' --require-conformant" \
    <scripts/jobseek-host-hygiene.py
  exit 0
fi

if [[ "$MODE" == apply ]]; then
  [[ $# -eq 4 ]]
  deploy_sha="$3"
  stage_id="$4"
  [[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]]
  [[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
  [[ "$stage_id" == "$deploy_sha"-* ]]
  stage="/var/lib/jobseek-host-hygiene/staging/$stage_id"

  # Create a fresh root-only boundary before any archive reaches the host.
  # shellcheck disable=SC2029
  remote 5m "bash -s -- '$stage_id'" <<'REMOTE_PREPARE'
set -euo pipefail
umask 077
stage_id="$1"
[[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
trust_root=/var/lib/jobseek-host-hygiene
staging_root="${trust_root}/staging"
stage="${staging_root}/${stage_id}"
verify_directory() {
  local directory="$1"
  [[ -d "$directory" && ! -L "$directory" ]]
  [[ "$(stat -c '%u:%g:%a' -- "$directory")" == 0:0:700 ]]
}
for directory in "$trust_root" "$staging_root"; do
  if [[ -e "$directory" || -L "$directory" ]]; then
    verify_directory "$directory"
  else
    install -d -o root -g root -m 0700 -- "$directory"
    verify_directory "$directory"
  fi
done
[[ ! -e "$stage" && ! -L "$stage" ]]
install -d -o root -g root -m 0700 -- "$stage"
verify_directory "$stage"
REMOTE_PREPARE

  # Archive transport and extraction both stay inside the verified root-only path.
  tar --create --gzip --file - \
    deploy/host-hygiene \
    scripts/jobseek-host-hygiene.py |
    remote 5m \
      "tar --extract --gzip --file - --directory '$stage' --no-same-owner --no-same-permissions --keep-old-files"

  # Re-establish the trust boundary after transfer, then execute only verified files.
  # shellcheck disable=SC2029
  remote 10m "bash -s -- '$ROLE' '$deploy_sha' '$stage_id'" <<'REMOTE_INSTALL'
set -euo pipefail
role="$1"
deploy_sha="$2"
stage_id="$3"
case "$role" in
  crawler|postgresql|typesense) ;;
  *) exit 2 ;;
esac
[[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
[[ "$stage_id" == "$deploy_sha"-* ]]
trust_root=/var/lib/jobseek-host-hygiene
staging_root="${trust_root}/staging"
stage="${staging_root}/${stage_id}"
for directory in "$trust_root" "$staging_root" "$stage"; do
  [[ -d "$directory" && ! -L "$directory" ]]
  [[ "$(stat -c '%u:%g:%a' -- "$directory")" == 0:0:700 ]]
done
if find "$stage" -xdev -type l -print -quit | grep -q .; then
  echo "ERROR: staging payload contains a symlink" >&2
  exit 1
fi
if find "$stage" -xdev \( ! -user root -o ! -group root \) -print -quit | grep -q .; then
  echo "ERROR: staging payload ownership is not root:root" >&2
  exit 1
fi
chmod -R go-rwx -- "$stage"
installer="$stage/deploy/host-hygiene/install-host.sh"
verifier="$stage/scripts/jobseek-host-hygiene.py"
for payload in "$installer" "$verifier"; do
  [[ -f "$payload" && ! -L "$payload" ]]
  [[ "$(stat -c '%u:%g' -- "$payload")" == 0:0 ]]
done
export JOBSEEK_HOST_HYGIENE_DEPLOY_SHA="$deploy_sha"
bash --noprofile --norc "$installer" "$role"
/usr/local/sbin/jobseek-host-hygiene audit --role "$role"
REMOTE_INSTALL
  exit 0
fi

[[ "$MODE" == cleanup && "$ROLE" == postgresql && $# -eq 7 ]]
container_id="$3"
image_id="$4"
created_at="$5"
finished_at="$6"
exit_code="$7"
timestamp_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z$'
[[ "$container_id" =~ ^[0-9a-f]{64}$ ]]
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$created_at" =~ $timestamp_re ]]
[[ "$finished_at" =~ $timestamp_re ]]
[[ "$exit_code" =~ ^-?[0-9]+$ ]]

# Every interpolated argument is constrained to a non-shell metacharacter alphabet above.
# shellcheck disable=SC2029
remote 5m \
  "bash -s -- '$container_id' '$image_id' '$created_at' '$finished_at' '$exit_code'" \
  <<'REMOTE_CLEANUP'
set -euo pipefail
container_id="$1"
image_id="$2"
created_at="$3"
finished_at="$4"
exit_code="$5"
timestamp_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z$'
[[ "$container_id" =~ ^[0-9a-f]{64}$ ]]
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$created_at" =~ $timestamp_re ]]
[[ "$finished_at" =~ $timestamp_re ]]
[[ "$exit_code" =~ ^-?[0-9]+$ ]]
command=(
  /usr/local/sbin/jobseek-host-hygiene remove-exited-container
  --role postgresql
  --container-id "$container_id"
  --image-id "$image_id"
  --created-at "$created_at"
  --finished-at "$finished_at"
  --exit-code "$exit_code"
)
"${command[@]}"
"${command[@]}" --execute
/usr/local/sbin/jobseek-host-hygiene audit --role postgresql --require-conformant
REMOTE_CLEANUP

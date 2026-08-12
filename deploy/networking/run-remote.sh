#!/usr/bin/env bash
# Run ingress audit/apply transactions over host-key-pinned native OpenSSH.
set -euo pipefail
umask 077

MODE="${1:-}"
ROLE="${2:-}"
case "$MODE" in
  audit|stage|verify-private-paths|rollback|commit) ;;
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

ssh_root="$(mktemp -d "$RUNNER_TEMP/jobseek-ingress-ssh.XXXXXX")"
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

require_private_ipv4() {
  local value="$1"
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
  [[ "$value" =~ ^10\. || "$value" =~ ^192\.168\. || "$value" =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ]]
}

require_deploy_identity() {
  local deploy_sha="$1"
  local stage_id="$2"
  [[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]]
  [[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
  [[ "$stage_id" == "$deploy_sha"-* ]]
}

if [[ "$MODE" == audit ]]; then
  [[ $# -eq 3 ]]
  crawler_private_ip="$3"
  require_private_ipv4 "$crawler_private_ip"
  # ROLE and the address are constrained to non-shell metacharacter alphabets above.
  # shellcheck disable=SC2029
  remote 5m \
    "python3 - --role '$ROLE' --crawler-private-ip '$crawler_private_ip' --require-enforced" \
    <scripts/jobseek-ingress-conformance.py
  exit 0
fi

if [[ "$MODE" == stage ]]; then
  [[ $# -eq 6 ]]
  deploy_sha="$3"
  stage_id="$4"
  crawler_private_ip="$5"
  postgres_private_ip="$6"
  require_deploy_identity "$deploy_sha" "$stage_id"
  require_private_ipv4 "$crawler_private_ip"
  require_private_ipv4 "$postgres_private_ip"
  stage="/var/lib/jobseek-ingress/staging/$stage_id"

  payloads=(
    deploy/networking/install-host.sh
    deploy/networking/harden-postgresql.sh
    deploy/networking/verify-private-paths.sh
    scripts/jobseek-ingress-conformance.py
  )
  payload_hashes=()
  for payload in "${payloads[@]}"; do
    [[ -f "$payload" && ! -L "$payload" ]]
    payload_hashes+=("$(sha256sum "$payload" | awk '{print $1}')")
  done
  for digest in "${payload_hashes[@]}"; do
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
  done

  # Create a fresh root-only boundary before any archive reaches the host.
  # shellcheck disable=SC2029
  remote 5m "bash -s -- '$stage_id'" <<'REMOTE_PREPARE'
set -euo pipefail
umask 077
stage_id="$1"
[[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
trust_root=/var/lib/jobseek-ingress
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

  # Extraction cannot overwrite a pre-existing entry in the fresh root-only stage.
  tar --create --gzip --file - "${payloads[@]}" |
    remote 5m \
      "tar --extract --gzip --file - --directory '$stage' --no-same-owner --no-same-permissions --keep-old-files"

  # Recheck the boundary and exact payload digests before any uploaded file executes.
  # shellcheck disable=SC2029
  remote 20m \
    "bash -s -- '$ROLE' '$deploy_sha' '$stage_id' '$crawler_private_ip' '$postgres_private_ip' '${payload_hashes[0]}' '${payload_hashes[1]}' '${payload_hashes[2]}' '${payload_hashes[3]}'" \
    <<'REMOTE_STAGE'
set -euo pipefail
role="$1"
deploy_sha="$2"
stage_id="$3"
crawler_private_ip="$4"
postgres_private_ip="$5"
shift 5
expected_hashes=("$@")
case "$role" in
  crawler|postgresql|typesense) ;;
  *) exit 2 ;;
esac
[[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
[[ "$stage_id" == "$deploy_sha"-* ]]
for value in "$crawler_private_ip" "$postgres_private_ip"; do
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
done
[[ "${#expected_hashes[@]}" -eq 4 ]]
for digest in "${expected_hashes[@]}"; do
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
done

trust_root=/var/lib/jobseek-ingress
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
if find "$stage" -xdev ! \( -type d -o -type f \) -print -quit | grep -q .; then
  echo "ERROR: staging payload contains an unsupported file type" >&2
  exit 1
fi
chmod -R go-rwx -- "$stage"
payloads=(
  "$stage/deploy/networking/install-host.sh"
  "$stage/deploy/networking/harden-postgresql.sh"
  "$stage/deploy/networking/verify-private-paths.sh"
  "$stage/scripts/jobseek-ingress-conformance.py"
)
[[ "$(find "$stage" -xdev -type d -print | wc -l)" -eq 4 ]]
[[ "$(find "$stage" -xdev -type f -print | wc -l)" -eq "${#payloads[@]}" ]]
if find "$stage" -xdev -type d ! -perm 0700 -print -quit | grep -q .; then
  echo "ERROR: staging payload contains a directory without mode 0700" >&2
  exit 1
fi
for index in "${!payloads[@]}"; do
  payload="${payloads[$index]}"
  [[ -f "$payload" && ! -L "$payload" ]]
  [[ "$(stat -c '%u:%g' -- "$payload")" == 0:0 ]]
  [[ "$(sha256sum "$payload" | awk '{print $1}')" == "${expected_hashes[$index]}" ]]
done

rollback_pending() {
  local status=$?
  trap - ERR EXIT
  set +e
  if [[ "$role" == postgresql && -s /var/lib/jobseek-ingress/postgresql/pending ]]; then
    env \
      JOBSEEK_CRAWLER_PRIVATE_IP="$crawler_private_ip" \
      JOBSEEK_POSTGRES_PRIVATE_IP="$postgres_private_ip" \
      /usr/local/sbin/jobseek-postgresql-network rollback
  fi
  if [[ -s /var/lib/jobseek-ingress/pending ]]; then
    env \
      JOBSEEK_CRAWLER_PRIVATE_IP="$crawler_private_ip" \
      JOBSEEK_INGRESS_DEPLOY_SHA="$deploy_sha" \
      /usr/local/sbin/jobseek-ingress-baseline rollback "$role"
  fi
  exit "$status"
}
trap rollback_pending ERR EXIT

env \
  JOBSEEK_CRAWLER_PRIVATE_IP="$crawler_private_ip" \
  JOBSEEK_INGRESS_DEPLOY_SHA="$deploy_sha" \
  bash --noprofile --norc "${payloads[0]}" stage "$role"
if [[ "$role" == postgresql ]]; then
  if python3 "${payloads[3]}" \
    --role postgresql \
    --crawler-private-ip "$crawler_private_ip" \
    --require-enforced; then
    echo "PostgreSQL data-plane contract is already compliant; skipping replacement"
  else
    env \
      JOBSEEK_CRAWLER_PRIVATE_IP="$crawler_private_ip" \
      JOBSEEK_POSTGRES_PRIVATE_IP="$postgres_private_ip" \
      bash --noprofile --norc "${payloads[1]}" stage
  fi
fi
python3 "${payloads[3]}" \
  --role "$role" \
  --crawler-private-ip "$crawler_private_ip" \
  --require-enforced
trap - ERR EXIT
REMOTE_STAGE
  exit 0
fi

if [[ "$MODE" == verify-private-paths ]]; then
  [[ "$ROLE" == crawler && $# -eq 6 ]]
  deploy_sha="$3"
  stage_id="$4"
  postgres_private_ip="$5"
  typesense_private_ip="$6"
  require_deploy_identity "$deploy_sha" "$stage_id"
  require_private_ipv4 "$postgres_private_ip"
  require_private_ipv4 "$typesense_private_ip"
  verifier_hash="$(sha256sum deploy/networking/verify-private-paths.sh | awk '{print $1}')"
  [[ "$verifier_hash" =~ ^[0-9a-f]{64}$ ]]
  # shellcheck disable=SC2029
  remote 5m \
    "bash -s -- '$deploy_sha' '$stage_id' '$postgres_private_ip' '$typesense_private_ip' '$verifier_hash'" \
    <<'REMOTE_VERIFY'
set -euo pipefail
deploy_sha="$1"
stage_id="$2"
postgres_private_ip="$3"
typesense_private_ip="$4"
verifier_hash="$5"
[[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
[[ "$stage_id" == "$deploy_sha"-* ]]
[[ "$verifier_hash" =~ ^[0-9a-f]{64}$ ]]
for value in "$postgres_private_ip" "$typesense_private_ip"; do
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
done
trust_root=/var/lib/jobseek-ingress
staging_root="${trust_root}/staging"
stage="${staging_root}/${stage_id}"
for directory in "$trust_root" "$staging_root" "$stage"; do
  [[ -d "$directory" && ! -L "$directory" ]]
  [[ "$(stat -c '%u:%g:%a' -- "$directory")" == 0:0:700 ]]
done
verifier="$stage/deploy/networking/verify-private-paths.sh"
[[ -f "$verifier" && ! -L "$verifier" ]]
[[ "$(stat -c '%u:%g' -- "$verifier")" == 0:0 ]]
[[ "$(sha256sum "$verifier" | awk '{print $1}')" == "$verifier_hash" ]]
env \
  JOBSEEK_POSTGRES_PRIVATE_IP="$postgres_private_ip" \
  JOBSEEK_TYPESENSE_PRIVATE_IP="$typesense_private_ip" \
  bash --noprofile --norc "$verifier"
REMOTE_VERIFY
  exit 0
fi

if [[ "$MODE" == rollback ]]; then
  [[ $# -eq 6 ]]
  deploy_sha="$3"
  stage_id="$4"
  crawler_private_ip="$5"
  postgres_private_ip="$6"
  require_deploy_identity "$deploy_sha" "$stage_id"
  require_private_ipv4 "$crawler_private_ip"
  require_private_ipv4 "$postgres_private_ip"
  # shellcheck disable=SC2029
  remote 10m \
    "bash -s -- '$ROLE' '$deploy_sha' '$stage_id' '$crawler_private_ip' '$postgres_private_ip'" \
    <<'REMOTE_ROLLBACK'
set -euo pipefail
role="$1"
deploy_sha="$2"
stage_id="$3"
crawler_private_ip="$4"
postgres_private_ip="$5"
case "$role" in
  crawler|postgresql|typesense) ;;
  *) exit 2 ;;
esac
[[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
[[ "$stage_id" == "$deploy_sha"-* ]]
for value in "$crawler_private_ip" "$postgres_private_ip"; do
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
done
rollback_status=0
if [[ "$role" == postgresql && -s /var/lib/jobseek-ingress/postgresql/pending ]]; then
  env \
    JOBSEEK_CRAWLER_PRIVATE_IP="$crawler_private_ip" \
    JOBSEEK_POSTGRES_PRIVATE_IP="$postgres_private_ip" \
    /usr/local/sbin/jobseek-postgresql-network rollback || rollback_status=$?
fi
if [[ -s /var/lib/jobseek-ingress/pending ]]; then
  env \
    JOBSEEK_CRAWLER_PRIVATE_IP="$crawler_private_ip" \
    JOBSEEK_INGRESS_DEPLOY_SHA="$deploy_sha" \
    /usr/local/sbin/jobseek-ingress-baseline rollback "$role" || rollback_status=$?
fi
exit "$rollback_status"
REMOTE_ROLLBACK
  exit 0
fi

[[ "$MODE" == commit && $# -eq 6 ]]
deploy_sha="$3"
stage_id="$4"
crawler_private_ip="$5"
postgres_private_ip="$6"
require_deploy_identity "$deploy_sha" "$stage_id"
require_private_ipv4 "$crawler_private_ip"
require_private_ipv4 "$postgres_private_ip"
# shellcheck disable=SC2029
remote 10m \
  "bash -s -- '$ROLE' '$deploy_sha' '$stage_id' '$crawler_private_ip' '$postgres_private_ip'" \
  <<'REMOTE_COMMIT'
set -euo pipefail
role="$1"
deploy_sha="$2"
stage_id="$3"
crawler_private_ip="$4"
postgres_private_ip="$5"
case "$role" in
  crawler|postgresql|typesense) ;;
  *) exit 2 ;;
esac
[[ "$deploy_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$stage_id" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
[[ "$stage_id" == "$deploy_sha"-* ]]
for value in "$crawler_private_ip" "$postgres_private_ip"; do
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
done
env \
  JOBSEEK_CRAWLER_PRIVATE_IP="$crawler_private_ip" \
  JOBSEEK_INGRESS_DEPLOY_SHA="$deploy_sha" \
  /usr/local/sbin/jobseek-ingress-baseline commit "$role"
if [[ "$role" == postgresql && -s /var/lib/jobseek-ingress/postgresql/pending ]]; then
  env \
    JOBSEEK_CRAWLER_PRIVATE_IP="$crawler_private_ip" \
    JOBSEEK_POSTGRES_PRIVATE_IP="$postgres_private_ip" \
    /usr/local/sbin/jobseek-postgresql-network commit
elif [[ "$role" == postgresql ]]; then
  echo "PostgreSQL data-plane contract was already compliant; no replacement to commit"
fi
REMOTE_COMMIT

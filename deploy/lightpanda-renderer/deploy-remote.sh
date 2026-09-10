#!/usr/bin/env bash
# Validate and transport one bounded renderer release over host-key-pinned SSH.
set -euo pipefail
set +x
umask 077

: "${TARGET_HOST:?Murmur host is required}"
: "${SSH_PRIVATE_KEY:?SSH private key is required}"
: "${SSH_KNOWN_HOSTS:?pinned Murmur host keys are required}"
: "${GHCR_PULL_USERNAME:?GHCR pull username is required}"
: "${GHCR_PULL_TOKEN:?GHCR pull token is required}"
: "${LIGHTPANDA_B0_CA_CERT_PEM:?renderer CA certificate is required}"
: "${LIGHTPANDA_B0_SERVER_CERT_PEM:?renderer server certificate is required}"
: "${LIGHTPANDA_B0_SERVER_KEY_PEM:?renderer server private key is required}"
: "${LIGHTPANDA_B0_CLIENT_CERT_PEM:?renderer client certificate is required}"
: "${RUNNER_TEMP:?runner temporary directory is required}"

SOURCE_COMMIT="${1:-}"
IMAGE_REF="${2:-}"
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$IMAGE_REF" =~ ^ghcr\.io/colophon-group/jobseek-lightpanda-renderer@sha256:[0-9a-f]{64}$ ]] || exit 2
[[ "$TARGET_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || exit 2
[[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ ]] || exit 2
[[ "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ ]] || exit 2
[[ "$GHCR_PULL_USERNAME" =~ ^[A-Za-z0-9_-]+$ ]] || exit 2

owner="r${GITHUB_RUN_ID}a${GITHUB_RUN_ATTEMPT}"
release_id="sha-${SOURCE_COMMIT}-${owner}"
release_dir="/home/deploy/.local/share/jobseek-lightpanda/releases/${release_id}"
work_root="$(mktemp -d "$RUNNER_TEMP/jobseek-lightpanda-renderer.XXXXXX")"
ssh_root="$work_root/ssh"
payload_root="$work_root/payload"
payload_archive="$work_root/payload.tar.gz"
auth_payload="$work_root/auth"
remote_stage=""

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [[ -n "$remote_stage" && "$remote_stage" =~ ^/tmp/jobseek-lightpanda-renderer\.${owner}\.[A-Za-z0-9]+$ ]]; then
    ssh "${ssh_options[@]}" "deploy@$TARGET_HOST" \
      "rm -rf -- '$remote_stage'" >/dev/null 2>&1 || :
  fi
  rm -rf -- "$work_root"
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

install -d -m 0700 "$ssh_root" "$payload_root" "$payload_root/pki"
printf '%s\n' "$SSH_PRIVATE_KEY" >"$ssh_root/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" >"$ssh_root/known_hosts"
chmod 0600 "$ssh_root/id" "$ssh_root/known_hosts"

# All four secrets are required and validated before the first SSH operation.
# The client private key is intentionally not a workflow input and cannot be
# included in this payload.
printf '%s' "$LIGHTPANDA_B0_CA_CERT_PEM" >"$payload_root/pki/ca.pem"
printf '%s' "$LIGHTPANDA_B0_SERVER_CERT_PEM" >"$payload_root/pki/server.pem"
printf '%s' "$LIGHTPANDA_B0_SERVER_KEY_PEM" >"$payload_root/pki/server-key.pem"
printf '%s' "$LIGHTPANDA_B0_CLIENT_CERT_PEM" >"$payload_root/pki/client.pem"
chmod 0600 "$payload_root/pki"/*

artifact_root=deploy/lightpanda-renderer
for artifact in compose.yml inventory.json verify.py validate_pki.py install-host.sh; do
  install -m 0600 "$artifact_root/$artifact" "$payload_root/$artifact"
done
python3 "$payload_root/validate_pki.py" \
  --ca "$payload_root/pki/ca.pem" \
  --server "$payload_root/pki/server.pem" \
  --server-key "$payload_root/pki/server-key.pem" \
  --client "$payload_root/pki/client.pem" \
  --output "$payload_root/pins.env"

cat >"$payload_root/release.env" <<EOF
RENDERER_IMAGE_REF=$IMAGE_REF
RENDERER_RELEASE_DIR=$release_dir
SOURCE_COMMIT=$SOURCE_COMMIT
RELEASE_ID=$release_id
EOF
cat "$payload_root/pins.env" >>"$payload_root/release.env"
chmod 0600 "$payload_root/pins.env" "$payload_root/release.env"

tar --create --gzip --file "$payload_archive" --directory "$payload_root" .
payload_size="$(stat -c '%s' "$payload_archive")"
[[ "$payload_size" =~ ^[0-9]+$ && "$payload_size" -le 262144 ]] || {
  echo "renderer deployment payload exceeds 256 KiB" >&2
  exit 1
}

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

remote_stage="$(ssh "${ssh_options[@]}" "deploy@$TARGET_HOST" \
  "umask 077; mktemp -d '/tmp/jobseek-lightpanda-renderer.${owner}.XXXXXX'")"
[[ "$remote_stage" =~ ^/tmp/jobseek-lightpanda-renderer\.${owner}\.[A-Za-z0-9]+$ ]] || exit 1

timeout --foreground --signal=TERM --kill-after=30s 5m \
  ssh "${ssh_options[@]}" "deploy@$TARGET_HOST" \
    "umask 077; tar --extract --gzip --file - --directory '$remote_stage' --no-same-owner --no-same-permissions" \
    <"$payload_archive"

{
  printf '%s' "$GHCR_PULL_USERNAME" | base64 | tr -d '\n'
  printf '\n'
  printf '%s' "$GHCR_PULL_TOKEN" | base64 | tr -d '\n'
  printf '\n'
} >"$auth_payload"
chmod 0600 "$auth_payload"

timeout --foreground --signal=TERM --kill-after=30s 20m \
  ssh "${ssh_options[@]}" "deploy@$TARGET_HOST" \
    "bash '$remote_stage/install-host.sh' '$remote_stage' '$SOURCE_COMMIT' '$IMAGE_REF' '$release_id'" \
    <"$auth_payload"

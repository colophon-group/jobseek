#!/usr/bin/env bash
# Native ARM64 build/start/inspect smoke with disposable test-only PKI.
set -euo pipefail
set +x
umask 077

IMAGE="${1:-}"
SOURCE_COMMIT="${2:-}"
[[ "$IMAGE" == jobseek-lightpanda-renderer:pr ]] || exit 2
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 2

ROOT=/home/deploy/.local/share/jobseek-lightpanda
RELEASE_ID="sha-${SOURCE_COMMIT}-r1a1"
RELEASE="$ROOT/releases/$RELEASE_ID"
NETWORK=jobseek-lightpanda-renderer
CONTAINER=jobseek-lightpanda-renderer
work=""
[[ ! -e "$ROOT" && ! -L "$ROOT" ]] || exit 1
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "CI renderer container already exists" >&2
  exit 1
fi
if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "CI renderer network already exists" >&2
  exit 1
fi

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  mapfile -t candidates < <(
    docker ps --all --quiet \
      --filter label=com.docker.compose.project=jobseek-lightpanda \
      --filter label=com.docker.compose.service=renderer \
      --filter "label=org.jobseek.lightpanda.release=$RELEASE_ID"
  )
  [[ "${#candidates[@]}" -le 1 ]] || exit 1
  if [[ "${#candidates[@]}" -eq 1 && "${candidates[0]}" =~ ^[0-9a-f]{64}$ ]]; then
    docker rm --force "${candidates[0]}" >/dev/null
  fi
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    network_id="$(docker network inspect --format '{{.Id}}' "$NETWORK")"
    network_project="$(docker network inspect --format '{{index .Labels "com.docker.compose.project"}}' "$network_id")"
    network_role="$(docker network inspect --format '{{index .Labels "com.docker.compose.network"}}' "$network_id")"
    [[ "$network_project" == jobseek-lightpanda && "$network_role" == renderer ]] || exit 1
    docker network rm "$network_id" >/dev/null
  fi
  sudo rm -rf -- "$ROOT"
  if [[ -n "$work" ]]; then
    rm -rf -- "$work"
  fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

sudo install -d -m 0711 "$ROOT" "$ROOT/releases" "$RELEASE" "$RELEASE/pki"
sudo install -m 0644 deploy/lightpanda-renderer/compose.yml "$RELEASE/compose.yml"
sudo install -m 0555 deploy/lightpanda-renderer/verify.py "$RELEASE/verify.py"
sudo install -m 0555 deploy/lightpanda-renderer/validate_pki.py "$RELEASE/validate_pki.py"

work="$(mktemp -d)"
cat >"$work/ca.cnf" <<'EOF'
[req]
distinguished_name=dn
x509_extensions=ca_ext
prompt=no
[dn]
CN=Jobseek Lightpanda B0 CI CA
[ca_ext]
basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always
EOF
cat >"$work/leaf.cnf" <<'EOF'
[server]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=IP:10.0.0.5
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
[client]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=clientAuth
subjectAltName=URI:spiffe://jobseek/crawler/lightpanda-b0
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
openssl req -new -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
  -pkeyopt ec_param_enc:named_curve -nodes -sha256 -days 1095 -config "$work/ca.cnf" \
  -keyout "$work/ca-key.pem" -out "$work/ca.pem" >/dev/null 2>&1
for leaf in server client; do
  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
    -pkeyopt ec_param_enc:named_curve -nodes \
    -subj "/CN=$leaf" -keyout "$work/$leaf-key.pem" -out "$work/$leaf.csr" \
    >/dev/null 2>&1
  openssl x509 -req -in "$work/$leaf.csr" -CA "$work/ca.pem" \
    -CAkey "$work/ca-key.pem" -CAcreateserial -days 180 -sha256 \
    -extfile "$work/leaf.cnf" -extensions "$leaf" -out "$work/$leaf.pem" \
    >/dev/null 2>&1
done
python3 deploy/lightpanda-renderer/validate_pki.py \
  --ca "$work/ca.pem" --server "$work/server.pem" \
  --server-key "$work/server-key.pem" --client "$work/client.pem" \
  --output "$work/pins.env"
sudo install -m 0444 "$work/ca.pem" "$RELEASE/pki/ca.pem"
sudo install -m 0444 "$work/server.pem" "$RELEASE/pki/server.pem"
sudo install -o 10001 -g 10001 -m 0400 "$work/server-key.pem" "$RELEASE/pki/server-key.pem"
sudo install -m 0644 "$work/pins.env" "$RELEASE/pins.env"

cat >"$work/release.env" <<EOF
RENDERER_IMAGE_REF=ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:$(printf 'a%.0s' {1..64})
RENDERER_RELEASE_DIR=$RELEASE
SOURCE_COMMIT=$SOURCE_COMMIT
RELEASE_ID=$RELEASE_ID
EOF
cat "$work/pins.env" >>"$work/release.env"
sudo install -o "$(id -u)" -g "$(id -g)" -m 0600 \
  "$work/release.env" "$RELEASE/release.env"

# Render and verify the real Compose file with an immutable-shaped identity.
python3 "$RELEASE/verify.py" compose "$RELEASE/compose.yml" "$RELEASE/release.env"
# The local smoke image has no registry digest. Only this CI-only environment
# substitution changes; all rendered containment and network fields stay exact.
sed -i "s|^RENDERER_IMAGE_REF=.*|RENDERER_IMAGE_REF=$IMAGE|" "$RELEASE/release.env"
docker compose --project-name jobseek-lightpanda \
  --env-file "$RELEASE/release.env" --file "$RELEASE/compose.yml" \
  up --detach --no-deps renderer
container_id="$(docker inspect --format '{{.Id}}' "$CONTAINER")"
image_id="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
[[ "$container_id" =~ ^[0-9a-f]{64}$ && "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
python3 - "$RELEASE/verify.py" "$RELEASE/release.env" "$image_id" <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("renderer_verify", sys.argv[1])
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
container_id = module.verify_running_with_image(
    Path(sys.argv[2]), image_id=sys.argv[3], expected_id=None
)
assert len(container_id) == 64
PY
test "$(docker image inspect --format '{{.Architecture}}' "$IMAGE")" = arm64
test "$(docker image inspect --format '{{index .Config.Labels "org.jobseek.lightpanda.source-commit"}}' "$IMAGE")" = "$SOURCE_COMMIT"

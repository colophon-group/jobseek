#!/usr/bin/env bash
# Keep the immutable web PostgreSQL client image reachable across Docker image GC.
set -euo pipefail

IMAGE="${1:-}"
LEASE_NAME="jobseek-web-postgresql-backup-image-lease"
LEASE_LABEL="jobseek.backup.helper-image=web-postgresql"
LEASE_TMPFS="/var/lib/postgresql/data:rw,noexec,nosuid,nodev,size=65536"
PULL_TIMEOUT_S=300

if [[ ! "$IMAGE" =~ ^[a-z0-9]+([._/-][a-z0-9]+)*(:[A-Za-z0-9_.-]+)?@sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: web PostgreSQL client image must be an exact sha256 digest reference" >&2
  exit 2
fi
lease_metadata() {
  docker container inspect \
    --format '{{.Config.Image}}|{{.State.Running}}|{{index .Config.Labels "jobseek.backup.helper-image"}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}|{{json .HostConfig.SecurityOpt}}|{{json .HostConfig.Tmpfs}}|{{json .Mounts}}|{{json .Config.Entrypoint}}' \
    "$1"
}

lease_is_exact() {
  local actual expected
  expected="${IMAGE}|false|web-postgresql|none|true|[\"ALL\"]|[\"no-new-privileges:true\"]|{\"/var/lib/postgresql/data\":\"rw,noexec,nosuid,nodev,size=65536\"}|[]|[\"/bin/true\"]"
  actual="$(lease_metadata "$1")"
  if [[ "$actual" != "$expected" ]]; then
    echo "ERROR: helper-image lease metadata differs from the hardened contract: $actual" >&2
    return 1
  fi
}

# Pulling by digest can update no mutable name. The explicit timeout bounds a
# registry outage during deployment and leaves any existing exact lease intact.
timeout --foreground --signal=TERM --kill-after=10s "${PULL_TIMEOUT_S}s" \
  docker pull --quiet "$IMAGE" >/dev/null
docker image inspect "$IMAGE" >/dev/null

if lease_is_exact "$LEASE_NAME" 2>/dev/null; then
  exit 0
fi

candidate="${LEASE_NAME}.candidate.$$"
candidate_exists=0
cleanup() {
  if [[ "$candidate_exists" -eq 1 ]]; then
    docker rm -- "$candidate" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

docker create \
  --name "$candidate" \
  --pull=never \
  --network none \
  --read-only \
  --tmpfs "$LEASE_TMPFS" \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --label "$LEASE_LABEL" \
  --entrypoint /bin/true \
  "$IMAGE" >/dev/null
candidate_exists=1
lease_is_exact "$candidate"

if existing_metadata="$(lease_metadata "$LEASE_NAME" 2>/dev/null)"; then
  existing_label="${existing_metadata#*|}"
  existing_label="${existing_label#*|}"
  existing_label="${existing_label%%|*}"
  existing_running="${existing_metadata#*|}"
  existing_running="${existing_running%%|*}"
  if [[ "$existing_label" != web-postgresql || "$existing_running" != false ]]; then
    echo "ERROR: refusing to replace an unmanaged or running helper-image lease" >&2
    exit 1
  fi
  # From this point onward, every failure path must retain the candidate. It is
  # the only new-image reference as soon as removal of the old lease begins.
  candidate_exists=0
  docker rm -- "$LEASE_NAME" >/dev/null
else
  candidate_exists=0
fi

# The candidate references the new digest before an older managed lease is
# removed. Preserve that candidate if the final rename fails: it is not the
# canonical lease, so readiness and monitoring still fail closed, but Docker GC
# cannot recreate the missing-image outage while an operator retries repair.
if ! docker rename "$candidate" "$LEASE_NAME"; then
  echo "ERROR: canonical helper-image lease rename failed; protected candidate remains: $candidate" >&2
  exit 1
fi
lease_is_exact "$LEASE_NAME"

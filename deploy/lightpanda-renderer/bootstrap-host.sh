#!/usr/bin/env bash
# One-way root maintenance bootstrap for the persistent renderer network boundary.
set -euo pipefail
set +x
umask 077

trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

STAGE="${1:-}"
SOURCE_COMMIT="${2:-}"
STATE_ROOT=/var/lib/jobseek-lightpanda-network
RELEASE_ROOT="$STATE_ROOT/releases"
GENERATION="$RELEASE_ROOT/sha-$SOURCE_COMMIT"
MARKER="$STATE_ROOT/bootstrap-complete.json"
POLICY=/usr/local/libexec/jobseek-lightpanda-network-policy
INVENTORY=/etc/jobseek-lightpanda-network/inventory.json
UNIT=/etc/systemd/system/jobseek-lightpanda-network.service
SUDOERS=/etc/sudoers.d/jobseek-lightpanda-network
HOST_LOCK=/run/lock/jobseek-lightpanda-network.lock
RENDERER_ROOT=/home/deploy/.local/share/jobseek-lightpanda
RENDERER_LOCK="$RENDERER_ROOT/renderer.lock"
ACTIVE="$RENDERER_ROOT/active"
CONTAINER=jobseek-lightpanda-renderer
EGRESS_NETWORK=jobseek-lightpanda-egress

[[ "$(id -u)" -eq 0 ]] || { echo "host bootstrap requires root" >&2; exit 2; }
[[ "$STAGE" =~ ^/tmp/jobseek-lightpanda-bootstrap\.r[0-9]+a[0-9]+\.[A-Za-z0-9]+$ ]] || exit 2
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$GENERATION" == "$RELEASE_ROOT/sha-$SOURCE_COMMIT" ]] || exit 2
for command in docker flock install python3 readlink runuser stat systemctl visudo; do
  command -v "$command" >/dev/null || {
    echo "required host bootstrap command is absent: $command" >&2
    exit 1
  }
done
for artifact in \
  network-policy.py \
  inventory.json \
  verify.py \
  jobseek-lightpanda-network.service \
  jobseek-lightpanda-network.sudoers; do
  [[ -f "$STAGE/$artifact" && ! -L "$STAGE/$artifact" ]] || exit 2
  [[ "$(stat -c '%s' "$STAGE/$artifact")" -le 262144 ]] || exit 2
done

install -d -o root -g root -m 0700 "$STATE_ROOT" "$RELEASE_ROOT"
install -d -o deploy -g deploy -m 0700 "$RENDERER_ROOT"
python3 - "$HOST_LOCK" "$RENDERER_LOCK" <<'PY'
import os
import pwd
import sys

deploy = pwd.getpwnam("deploy")
for path, mode, uid, gid in (
    (sys.argv[1], 0o640, 0, deploy.pw_gid),
    (sys.argv[2], 0o600, deploy.pw_uid, deploy.pw_gid),
):
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            mode,
        )
    except FileExistsError:
        continue
    try:
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
    finally:
        os.close(descriptor)
PY
[[ -f "$HOST_LOCK" && ! -L "$HOST_LOCK" ]] || exit 1
[[ "$(stat -c '%U:%G:%a:%h' "$HOST_LOCK")" == root:deploy:640:1 ]] || exit 1
[[ -f "$RENDERER_LOCK" && ! -L "$RENDERER_LOCK" ]] || exit 1
[[ "$(stat -c '%U:%G:%a:%h' "$RENDERER_LOCK")" == deploy:deploy:600:1 ]] || exit 1
exec 9<"$HOST_LOCK"
flock -w 900 9 || { echo "host policy lock is busy" >&2; exit 1; }
exec 8<"$RENDERER_LOCK"
flock -w 300 8 || { echo "renderer lock is busy" >&2; exit 1; }

protected_before="$STAGE/protected-before.json"
python3 "$STAGE/verify.py" snapshot-protected "$protected_before"

stable_empty_egress() {
  for _scan in 1 2; do
    if docker network inspect "$EGRESS_NETWORK" >/dev/null 2>&1; then
      endpoint_count="$(docker network inspect --format '{{len .Containers}}' "$EGRESS_NETWORK")"
      [[ "$endpoint_count" == 0 ]] || return 1
    fi
  done
}

failure_containment() {
  status=$?
  trap - EXIT HUP INT TERM
  if [[ "$status" -ne 0 ]]; then
    containment_policy="$STAGE/network-policy.py"
    [[ -x "$GENERATION/network-policy.py" ]] && containment_policy="$GENERATION/network-policy.py"
    python3 "$containment_policy" quarantine || status=1
    stable_empty_egress || status=1
    python3 "$STAGE/verify.py" assert-protected "$protected_before" || status=1
  fi
  exit "$status"
}
trap failure_containment EXIT

previous_generation=""
if [[ -e "$ACTIVE" || -L "$ACTIVE" ]]; then
  [[ -L "$ACTIVE" ]] || exit 1
  previous_generation="$(readlink -f "$ACTIVE")"
  [[ "$previous_generation" =~ ^${RENDERER_ROOT}/releases/sha-[0-9a-f]{40}-(ci-)?r[0-9]+a[0-9]+$ ]] || exit 1
  [[ -d "$previous_generation" && ! -L "$previous_generation" ]] || exit 1
  for artifact in compose.yml release.env verify.py; do
    [[ -f "$previous_generation/$artifact" && ! -L "$previous_generation/$artifact" ]] || exit 1
  done
fi

if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  [[ -n "$previous_generation" ]] || exit 1
  previous_id="$(docker inspect --format '{{.Id}}' "$CONTAINER")"
  [[ "$previous_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
  runuser -u deploy -- python3 "$previous_generation/verify.py" running \
    "$previous_generation/release.env" --expected-id "$previous_id" >/dev/null
  docker stop --time 30 "$previous_id" >/dev/null
  [[ "$(docker inspect --format '{{json .State.Running}}' "$previous_id")" == false ]] || exit 1
  docker rm "$previous_id" >/dev/null
  ! docker inspect "$CONTAINER" >/dev/null 2>&1 || exit 1
fi

# The active pointer and prior release deliberately remain in place. Bootstrap
# only removes the verified process, so an ordinary renderer deploy can still
# identify and restore the exact legacy generation if its candidate fails.
stable_empty_egress
python3 "$STAGE/verify.py" assert-protected "$protected_before"

install -d -o root -g root -m 0755 "$GENERATION"
install -o root -g root -m 0555 "$STAGE/network-policy.py" "$GENERATION/network-policy.py"
install -o root -g root -m 0444 "$STAGE/inventory.json" "$GENERATION/inventory.json"
install -D -o root -g root -m 0644 "$STAGE/jobseek-lightpanda-network.service" "$UNIT"
install -D -o root -g root -m 0440 "$STAGE/jobseek-lightpanda-network.sudoers" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

python3 - "$POLICY" "$GENERATION/network-policy.py" "$INVENTORY" "$GENERATION/inventory.json" <<'PY'
import os
import secrets
import sys

for target, source in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    parent = os.path.dirname(target)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    temporary = os.path.join(parent, f".{os.path.basename(target)}.{secrets.token_hex(8)}")
    os.symlink(source, temporary)
    os.replace(temporary, target)
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY

python3 - "$GENERATION" /usr/local/libexec /etc/jobseek-lightpanda-network /etc/systemd/system /etc/sudoers.d <<'PY'
import os
import sys

for path in sys.argv[1:]:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY

"$POLICY" bootstrap >/dev/null
"$POLICY" verify >/dev/null
systemctl daemon-reload
systemctl enable jobseek-lightpanda-network.service >/dev/null
systemctl restart jobseek-lightpanda-network.service
systemctl is-enabled --quiet jobseek-lightpanda-network.service
systemctl is-active --quiet jobseek-lightpanda-network.service
[[ "$(systemctl show --property=Result --value jobseek-lightpanda-network.service)" == success ]]
"$POLICY" verify >/dev/null
stable_empty_egress
python3 "$STAGE/verify.py" assert-protected "$protected_before"

python3 - "$MARKER" "$SOURCE_COMMIT" "$GENERATION/network-policy.py" "$GENERATION/inventory.json" <<'PY'
import hashlib
import json
import os
import secrets
import sys

path, source_commit, policy_path, inventory_path = sys.argv[1:]
payload = {
    "schema_version": 1,
    "source_commit": source_commit,
    "policy_sha256": hashlib.sha256(open(policy_path, "rb").read()).hexdigest(),
    "inventory_sha256": hashlib.sha256(open(inventory_path, "rb").read()).hexdigest(),
}
parent = os.path.dirname(path)
temporary = os.path.join(parent, f".bootstrap-complete.{secrets.token_hex(8)}")
descriptor = os.open(
    temporary,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
    0o600,
)
try:
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    while data:
        written = os.write(descriptor, data)
        if written <= 0:
            raise OSError("short bootstrap marker write")
        data = data[written:]
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(temporary, path)
directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY

"$POLICY" verify-ready >/dev/null

trap - EXIT
echo "Lightpanda host network bootstrap complete for $SOURCE_COMMIT"

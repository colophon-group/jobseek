#!/usr/bin/env bash
# Restore and validate one Typesense backup on a non-production Docker host.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: restore-drill.sh must run as root" >&2
  exit 1
fi

for command in curl docker flock python3 restic ss; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "ERROR: required command is missing: $command" >&2
    exit 1
  }
done

ENV_FILE="${JOBSEEK_TYPESENSE_RESTORE_ENV:-/etc/jobseek-backup/typesense.env}"
EXPECTED_INVENTORY="${JOBSEEK_TYPESENSE_EXPECTED_INVENTORY:-}"
RESTORE_ROOT="${JOBSEEK_TYPESENSE_RESTORE_ROOT:-/var/lib/jobseek-typesense-restore}"
READY_TIMEOUT_S="${JOBSEEK_TYPESENSE_RESTORE_READY_TIMEOUT_S:-1200}"
MIN_FREE_BYTES="${JOBSEEK_TYPESENSE_RESTORE_MIN_FREE_BYTES:-4294967296}"
IMAGE="${JOBSEEK_TYPESENSE_RESTORE_IMAGE:-typesense/typesense:27.1@sha256:5c12af89130b8ee0be11541321ba8a3a7c7a538d7c6cd95e0409dc2d75ca6455}"
PORT="${JOBSEEK_TYPESENSE_RESTORE_PORT:-18108}"
SNAPSHOT="${1:-latest}"
EXPECTED_ALIASES=(
  company
  job_posting
  location
  occupation
  seniority
  technology
  watchlist
)

[[ "$IMAGE" =~ ^typesense/typesense:27\.1@sha256:[0-9a-f]{64}$ ]] || {
  echo "ERROR: Typesense restore image must be a reviewed digest-pinned 27.1 artifact" >&2
  exit 1
}

if docker container inspect typesense >/dev/null 2>&1; then
  echo "ERROR: production container name 'typesense' exists; use an isolated recovery host" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" || -L "$ENV_FILE" ]]; then
  echo "ERROR: root-only restore environment is missing or unsafe" >&2
  exit 1
fi
if [[ "$(stat -c %U:%G "$ENV_FILE")" != "root:root" ]] ||
  (( 8#$(stat -c %a "$ENV_FILE") & 8#077 ))
then
  echo "ERROR: restore environment must be root:root and inaccessible to group/other" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
for variable in RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_SFTP_COMMAND; do
  [[ -n "${!variable:-}" ]] || {
    echo "ERROR: $variable is missing" >&2
    exit 1
  }
done
if [[ -n "$EXPECTED_INVENTORY" ]] &&
  [[ ! -f "$EXPECTED_INVENTORY" || -L "$EXPECTED_INVENTORY" ]]
then
  echo "ERROR: expected inventory is missing or unsafe" >&2
  exit 1
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
  echo "ERROR: restore port must be an unprivileged TCP port" >&2
  exit 1
fi
if [[ ! "$READY_TIMEOUT_S" =~ ^[0-9]+$ ]] || (( READY_TIMEOUT_S < 30 )); then
  echo "ERROR: restore readiness timeout is invalid" >&2
  exit 1
fi
if [[ ! "$MIN_FREE_BYTES" =~ ^[0-9]+$ ]]; then
  echo "ERROR: restore minimum free bytes is invalid" >&2
  exit 1
fi

install -d -o root -g root -m 0700 "$RESTORE_ROOT"
available_bytes="$(df --output=avail -B1 "$RESTORE_ROOT" | tail -n 1 | tr -d ' ')"
if (( available_bytes < MIN_FREE_BYTES )); then
  echo "ERROR: fewer than $MIN_FREE_BYTES bytes are free for the restore" >&2
  exit 1
fi
if ss -H -ltn "sport = :$PORT" | grep -q .; then
  echo "ERROR: restore port $PORT is already in use" >&2
  exit 1
fi

exec 9>/run/jobseek-typesense-restore-drill.lock
if ! flock -n 9; then
  echo "ERROR: another Typesense restore drill is active" >&2
  exit 1
fi

work_dir="$(mktemp -d "$RESTORE_ROOT/run.XXXXXX")"
container="jobseek-typesense-restore-${RANDOM}-$$"
config="$work_dir/typesense-server.ini"
result="$work_dir/result.json"
started_unix="$(date +%s)"

cleanup() {
  local status=$?
  docker rm --force "$container" >/dev/null 2>&1 || true
  rm -rf -- "$work_dir"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

restic_command() {
  restic -o "sftp.command=$RESTIC_SFTP_COMMAND" "$@"
}

if [[ "$SNAPSHOT" == "latest" ]]; then
  snapshot_arguments=(
    snapshots
    --json
    --latest
    1
    --tag
    jobseek-typesense
    --host
    jobseek-typesense
  )
else
  snapshot_arguments=(snapshots --json "$SNAPSHOT")
fi
snapshots_json="$(restic_command "${snapshot_arguments[@]}")"
snapshot_metadata="$(python3 -c '
import json
import sys

rows = json.loads(sys.argv[1])
if not rows:
    raise SystemExit("ERROR: no Typesense snapshot was resolved")
row = max(rows, key=lambda item: item.get("time") or "")
identifier = row.get("id") or ""
if not identifier:
    raise SystemExit("ERROR: snapshot has no identifier")
print(json.dumps({
    "id": identifier,
    "short_id": row.get("short_id") or identifier[:8],
    "time": row.get("time"),
}, separators=(",", ":")))
' "$snapshots_json")"
snapshot_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$snapshot_metadata")"

restic_command restore "$snapshot_id" --target "$work_dir/repository" >&2
mapfile -t candidates < <(
  find "$work_dir/repository/var/lib/jobseek-backup/typesense/staging" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null
)
if [[ "${#candidates[@]}" -ne 1 ]]; then
  echo "ERROR: restored snapshot did not contain one Typesense data directory" >&2
  exit 1
fi
data_dir="$(realpath -e "${candidates[0]}")"
case "$data_dir/" in
  "$work_dir/"*) ;;
  *)
    echo "ERROR: restored data escaped the temporary root" >&2
    exit 1
    ;;
esac
restored_bytes="$(du -sb "$data_dir" | awk '{print $1}')"
if (( restored_bytes <= 0 )); then
  echo "ERROR: restored Typesense snapshot is empty" >&2
  exit 1
fi

temporary_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
{
  printf '%s\n' '[server]'
  printf '%s\n' 'data-dir = /data'
  printf 'api-key = %s\n' "$temporary_key"
  printf '%s\n' 'api-port = 8108'
  printf '%s\n' 'listen-address = 0.0.0.0'
} >"$config"
chmod 0600 "$config"

docker_arguments=(
  run
  --detach
  --name
  "$container"
  --network
  bridge
  --publish
  "127.0.0.1:$PORT:8108"
  --ulimit
  nofile=65536:65536
  --memory
  6g
  --cpus
  4
  --volume
  "$data_dir:/data"
  --volume
  "$config:/run/secrets/typesense-server.ini:ro"
  --label
  jobseek.purpose=isolated-restore-drill
  "$IMAGE"
  --config=/run/secrets/typesense-server.ini
)
docker "${docker_arguments[@]}" >/dev/null

deadline=$((SECONDS + READY_TIMEOUT_S))
until curl --fail --silent --max-time 5 "http://127.0.0.1:$PORT/health" 2>/dev/null |
  python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("ok") is True else 1)' 2>/dev/null
do
  if ! docker container inspect --format '{{.State.Running}}' "$container" |
    grep -qx true
  then
    echo "ERROR: restored Typesense container exited before readiness" >&2
    docker logs --tail 40 "$container" >&2 || true
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    echo "ERROR: restored Typesense did not become ready in time" >&2
    docker logs --tail 40 "$container" >&2 || true
    exit 1
  fi
  sleep 2
done

export JOBSEEK_TYPESENSE_RESTORE_KEY="$temporary_key"
export JOBSEEK_TYPESENSE_RESTORE_URL="http://127.0.0.1:$PORT"
export JOBSEEK_TYPESENSE_EXPECTED_ALIASES
JOBSEEK_TYPESENSE_EXPECTED_ALIASES="$(IFS=,; echo "${EXPECTED_ALIASES[*]}")"
export JOBSEEK_TYPESENSE_EXPECTED_INVENTORY="$EXPECTED_INVENTORY"
export JOBSEEK_TYPESENSE_SNAPSHOT_METADATA="$snapshot_metadata"
export JOBSEEK_TYPESENSE_RESTORED_BYTES="$restored_bytes"
export JOBSEEK_TYPESENSE_RESTORE_STARTED="$started_unix"

python3 - <<'PY' >"$result"
import json
import os
import urllib.parse
import urllib.request

base = os.environ["JOBSEEK_TYPESENSE_RESTORE_URL"].rstrip("/")
key = os.environ["JOBSEEK_TYPESENSE_RESTORE_KEY"]
expected_aliases = set(os.environ["JOBSEEK_TYPESENSE_EXPECTED_ALIASES"].split(","))


def request(path: str, *, method: str = "GET", body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-TYPESENSE-API-KEY": key,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


health = request("/health")
if health.get("ok") is not True:
    raise SystemExit("ERROR: restored Typesense health check failed")
debug = request("/debug")
if int(debug.get("state") or 0) != 1:
    raise SystemExit("ERROR: restored Typesense is not the single-node leader")

alias_rows = request("/aliases").get("aliases")
if not isinstance(alias_rows, list):
    raise SystemExit("ERROR: restored alias response is invalid")
aliases = {row["name"]: row["collection_name"] for row in alias_rows}
if set(aliases) != expected_aliases:
    raise SystemExit("ERROR: restored aliases do not match the required set")

counts = {}
for alias in sorted(expected_aliases):
    collection = request(f"/collections/{urllib.parse.quote(alias)}")
    count = int(collection["num_documents"])
    if count < 0:
        raise SystemExit(f"ERROR: restored {alias} count is negative")
    counts[alias] = count

expected_path = os.environ.get("JOBSEEK_TYPESENSE_EXPECTED_INVENTORY")
if expected_path:
    with open(expected_path, encoding="utf-8") as stream:
        expected = json.load(stream)
    if aliases != expected.get("aliases"):
        raise SystemExit("ERROR: restored alias targets differ from production evidence")
    if counts != expected.get("collection_documents"):
        mismatches = {
            name: {
                "expected": expected.get("collection_documents", {}).get(name),
                "restored": counts.get(name),
            }
            for name in sorted(expected_aliases)
            if expected.get("collection_documents", {}).get(name) != counts.get(name)
        }
        raise SystemExit(
            "ERROR: restored collection counts differ from checkpoint evidence: "
            + json.dumps(mismatches, sort_keys=True)
        )

search = request(
    "/collections/job_posting/documents/search?"
    + urllib.parse.urlencode({"q": "*", "query_by": "title", "per_page": 1})
)
if int(search.get("found") or 0) != counts["job_posting"] or not search.get("hits"):
    raise SystemExit("ERROR: representative restored posting query failed")

probe = "jobseek_restore_probe"
try:
    request(
        "/collections",
        method="POST",
        body={
            "name": probe,
            "fields": [{"name": "name", "type": "string"}],
        },
    )
    request(
        f"/collections/{probe}/documents",
        method="POST",
        body={"id": "probe", "name": "isolated restore"},
    )
    document = request(f"/collections/{probe}/documents/probe")
    if document.get("id") != "probe":
        raise SystemExit("ERROR: restored node write/read probe failed")
finally:
    try:
        request(f"/collections/{probe}", method="DELETE")
    except Exception:
        pass

snapshot = json.loads(os.environ["JOBSEEK_TYPESENSE_SNAPSHOT_METADATA"])
started = int(os.environ["JOBSEEK_TYPESENSE_RESTORE_STARTED"])
import time

print(
    json.dumps(
        {
            "success": True,
            "snapshot_id": snapshot["short_id"],
            "snapshot_time": snapshot.get("time"),
            "restored_bytes": int(os.environ["JOBSEEK_TYPESENSE_RESTORED_BYTES"]),
            "duration_seconds": int(time.time()) - started,
            "aliases": aliases,
            "collection_documents": counts,
            "checks": [
                "health",
                "single_node_leader",
                "exact_alias_inventory",
                "exact_collection_counts",
                "representative_search",
                "ephemeral_write_read_delete",
            ],
        },
        indent=2,
        sort_keys=True,
    )
)
PY

cat "$result"

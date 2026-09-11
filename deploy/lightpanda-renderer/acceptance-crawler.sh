#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
STAGE="${1:-}"
OWNER="${2:-}"
[[ "$STAGE" =~ ^/tmp/jobseek-lightpanda-crawler-acceptance\.${OWNER}\.[A-Za-z0-9]+$ ]] || exit 2
[[ "$OWNER" =~ ^r[1-9][0-9]*a[1-9][0-9]*$ ]] || exit 2
[[ "$(id -un)" == deploy && "$(id -u)" -ne 0 ]] || exit 2
[[ -f "$STAGE/acceptance-client.py" && ! -L "$STAGE/acceptance-client.py" ]] || exit 2
exec 9>/run/lock/jobseek-crawler-mutation.lock
flock --shared --wait 300 9
env_file=/home/deploy/.env
credential_root=/home/deploy/.local/share/jobseek-lightpanda-claimant/credentials
[[ -f "$env_file" && ! -L "$env_file" && "$(stat -c '%u:%g:%a:%h' "$env_file")" == \
  "$(id -u):$(id -g):600:1" && "$(stat -c '%s' "$env_file")" -le 262144 ]] || exit 2
mapfile -t generations < <(sed -n 's/^LIGHTPANDA_B0_CREDENTIAL_DIR=//p' "$env_file")
[[ "${#generations[@]}" -eq 1 ]]
credential_dir="${generations[0]}"
[[ "$credential_dir" == "$credential_root"/* ]]
[[ "${credential_dir#"$credential_root"/}" =~ ^sha-[0-9a-f]{40}-[0-9a-f]{64}$ ]]
[[ "$(realpath -e -- "$credential_root")" == "$credential_root" && \
  "$(realpath -e -- "$credential_dir")" == "$credential_dir" && ! -L "$credential_dir" ]]
[[ "$(stat -c '%u:%g:%a' "$credential_dir")" == "$(id -u):$(id -g):711" ]]
expected=(ca.pem ca.sha256 client-key.pem client.pem server-leaf.sha256 server-spki.sha256)
mapfile -t members < <(find "$credential_dir" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)
[[ "${members[*]}" == "${expected[*]}" ]]
for name in "${expected[@]}"; do
  path="$credential_dir/$name"
  [[ -f "$path" && ! -L "$path" ]]
  metadata="$(stat -c '%u:%g:%a:%h' "$path")"
  if [[ "$name" == client-key.pem ]]; then
    [[ "$metadata" == 10001:10001:400:1 ]]
  else
    [[ "$metadata" == "$(id -u):$(id -g):444:1" ]]
  fi
done
container="jobseek-lightpanda-acceptance-$OWNER"
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  docker rm --force "$container" >/dev/null 2>&1 || :
  exit "$status"
}
trap cleanup EXIT HUP INT TERM
inspect="$STAGE/claimant.json"
mapfile -t claimant_ids < <(docker ps --all --no-trunc --quiet \
  --filter label=com.docker.compose.project=deploy \
  --filter label=com.docker.compose.service=lightpanda-claimant)
[[ "${#claimant_ids[@]}" -eq 1 && "${claimant_ids[0]}" =~ ^[0-9a-f]{64}$ ]]
docker inspect "${claimant_ids[0]}" >"$inspect"
mapfile -t plan < <(python3 - "$inspect" <<'PY'
import json, re, sys
item = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(item, list) or len(item) != 1:
    raise SystemExit("claimant inspect is not exact")
item = item[0]
state, host = item.get("State") or {}, item.get("HostConfig") or {}
image = str((item.get("Config") or {}).get("Image", ""))
container_id = str(item.get("Id", ""))
environment = (item.get("Config") or {}).get("Env") or []
if not isinstance(environment, list) or any(not isinstance(value, str) for value in environment):
    raise SystemExit("dark claimant environment is malformed")
names = [value.partition("=")[0] for value in environment]
authority = ("LOCAL_DATABASE_URL", "REDIS_URL", "R2_", "TYPESENSE_", "PROXY_", "WEBSHARE_", "MURMUR_")
if (
    re.fullmatch(r"[0-9a-f]{64}", container_id) is None
    or re.fullmatch(r"ghcr\.io/colophon-group/jobseek-crawler@sha256:[0-9a-f]{64}", image) is None
    or state.get("Running") is not True
    or (state.get("Health") or {}).get("Status") != "healthy"
    or host.get("NetworkMode") != "none"
    or item.get("Mounts") not in (None, [])
    or environment.count("LIGHTPANDA_B0_SUPERVISOR_MODE=dark") != 1
    or any(name.startswith("LIGHTPANDA_B0_") and name != "LIGHTPANDA_B0_SUPERVISOR_MODE" for name in names)
    or any(name == prefix or name.startswith(prefix) for name in names for prefix in authority)
):
    raise SystemExit("dark claimant identity drifted")
print(container_id)
print(image)
PY
)
[[ "${#plan[@]}" -eq 2 ]]
claimant_id="${plan[0]}"
image_ref="${plan[1]}"
client="$STAGE/client.json"
timeout --foreground --signal=TERM --kill-after=15s 90s \
  docker run --rm --name "$container" --network host --read-only \
    --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
    --memory 256m --memory-swap 256m --cpus 0.25 --pids-limit 32 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=8m,uid=10001,gid=10001,mode=0700 \
    --mount "type=bind,source=$STAGE/acceptance-client.py,target=/run/acceptance-client.py,readonly" \
    --mount "type=bind,source=$credential_dir,target=/run/credentials/lightpanda-b0,readonly" \
    --entrypoint /app/.venv/bin/python "$image_ref" \
    -B /run/acceptance-client.py >"$client"
test "$(docker inspect --format '{{.State.Running}}:{{.State.Health.Status}}:{{.Id}}' "$claimant_id")" = \
  "true:healthy:$claimant_id"
python3 - "$client" "$claimant_id" "$image_ref" <<'PY'
import json, re, sys
client = json.load(open(sys.argv[1], encoding="utf-8"))
if (
    client.get("status") != "accepted"
    or re.fullmatch(r"[0-9a-f]{64}", str(client.get("credential_bundle_sha256", ""))) is None
):
    raise SystemExit("mTLS client report is invalid")
print(json.dumps({
    **client,
    "claimant_container_id": sys.argv[2],
    "crawler_image_ref": sys.argv[3],
}, sort_keys=True, separators=(",", ":")))
PY

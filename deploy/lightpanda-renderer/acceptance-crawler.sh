#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
STAGE="${1:-}"
OWNER="${2:-}"
[[ "$STAGE" =~ ^/tmp/jobseek-lightpanda-crawler-acceptance\.${OWNER}\.[A-Za-z0-9]+$ ]] || exit 2
[[ "$OWNER" =~ ^r[1-9][0-9]*a[1-9][0-9]*$ ]] || exit 2
[[ "$(id -un)" == deploy && "$(id -u)" -ne 0 ]] || exit 2
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
inspect="$STAGE/worker.json"
image_inspect="$STAGE/image.json"
mapfile -t worker_ids < <(docker ps --all --no-trunc --quiet \
  --filter label=com.docker.compose.project=deploy \
  --filter label=com.docker.compose.service=ordinary-go)
[[ "${#worker_ids[@]}" -eq 1 && "${worker_ids[0]}" =~ ^[0-9a-f]{64}$ ]]
# Select only public identity fields; never copy the worker's environment or
# authority-bearing mounts into this standalone probe or its staged evidence.
docker inspect --format '{"id":{{json .Id}},"image_ref":{{json .Config.Image}},"image_id":{{json .Image}},"running":{{json .State.Running}},"health":{{json .State.Health.Status}}}' \
  "${worker_ids[0]}" >"$inspect"
mapfile -t plan < <(python3 - "$inspect" <<'PYWORKER'
import json, re, sys
item = json.load(open(sys.argv[1], encoding="utf-8"))
if (
    not isinstance(item, dict)
    or re.fullmatch(r"[0-9a-f]{64}", str(item.get("id", ""))) is None
    or re.fullmatch(r"ghcr\.io/colophon-group/jobseek-crawler@sha256:[0-9a-f]{64}", str(item.get("image_ref", ""))) is None
    or re.fullmatch(r"sha256:[0-9a-f]{64}", str(item.get("image_id", ""))) is None
    or item.get("running") is not True
    or item.get("health") != "healthy"
):
    raise SystemExit("native worker identity drifted")
print(item["id"])
print(item["image_ref"])
print(item["image_id"])
PYWORKER
)
[[ "${#plan[@]}" -eq 3 ]]
worker_id="${plan[0]}"
image_ref="${plan[1]}"
image_id="${plan[2]}"
docker image inspect --format '{"id":{{json .Id}},"labels":{{json .Config.Labels}}}' \
  "$image_ref" >"$image_inspect"
source_revision="$(python3 - "$image_inspect" "$image_id" <<'PYIMAGE'
import json, re, sys
item = json.load(open(sys.argv[1], encoding="utf-8"))
labels = item.get("labels") or {}
source = labels.get("org.opencontainers.image.revision", "")
if (
    item.get("id") != sys.argv[2]
    or labels.get("org.jobseek.crawler.default-native-runtime") != "v1"
    or re.fullmatch(r"[0-9a-f]{40}", str(source)) is None
):
    raise SystemExit("native image identity drifted")
print(source)
PYIMAGE
)"
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]]
client="$STAGE/client.json"
# The installed native release must contain this command; older images fail
# closed. No legacy claimant or Python runtime is an acceptance fallback.
timeout --foreground --signal=TERM --kill-after=15s 90s \
  docker run --rm --pull never --name "$container" --network host --read-only --cgroupns private \
    --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 \
    --memory 256m --memory-swap 256m --cpus 0.25 --pids-limit 32 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=8m,uid=10001,gid=10001,mode=0700 \
    --mount "type=bind,source=$credential_dir,target=/run/lightpanda,readonly" \
    --entrypoint /usr/local/bin/go-ordinary-worker "$image_ref" \
    --renderer-acceptance >"$client"
test "$(docker inspect --format '{{.State.Running}}:{{.State.Health.Status}}:{{.Id}}' "$worker_id")" = \
  "true:healthy:$worker_id"
python3 - "$client" "$worker_id" "$image_ref" "$source_revision" "$image_id" <<'PYREPORT'
import json, re, sys
client = json.load(open(sys.argv[1], encoding="utf-8"))
if (
    client.get("status") != "accepted"
    or client.get("target") != "https://example.com/"
    or client.get("http_status") != 200
    or client.get("crawler_source_revision") != sys.argv[4]
    or re.fullmatch(r"[0-9a-f]{64}", str(client.get("credential_bundle_sha256", ""))) is None
):
    raise SystemExit("mTLS client report is invalid")
print(json.dumps({
    **client,
    "ordinary_worker_container_id": sys.argv[2],
    "crawler_image_id": sys.argv[5],
    "crawler_image_ref": sys.argv[3],
}, sort_keys=True, separators=(",", ":")))
PYREPORT

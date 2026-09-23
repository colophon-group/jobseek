#!/usr/bin/env bash
# Requires Docker, curl and jq. Uses an empty, disposable local server only.
set -euo pipefail
image='typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110'
out=${1:-$(mktemp -d "${TMPDIR:-/tmp}/typesense-resident-results.XXXXXX")}
mkdir -p "$out"
container=''
cleanup() {
  if [ -n "$container" ]; then docker rm -fv "$container" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
container=$(docker run -d --publish 127.0.0.1::8108 \
  "$image" --data-dir=/tmp --api-key=repro-key)
port=$(docker inspect "$container" | jq -r '.[0].NetworkSettings.Ports["8108/tcp"][0].HostPort')
base="http://127.0.0.1:$port"
ready=false
for ((i=0; i<120; i++)); do
  if curl -fsS --max-time 2 "$base/health" 2>/dev/null | jq -e '.ok == true' >/dev/null; then
    ready=true
    break
  fi
  sleep 0.5
done
if [ "$ready" != true ]; then docker logs "$container" >&2; exit 1; fi
curl -fsS -H 'X-TYPESENSE-API-KEY: repro-key' "$base/debug" > "$out/debug.json"
curl -fsS -H 'X-TYPESENSE-API-KEY: repro-key' "$base/collections" > "$out/collections.json"
jq -e '.version == "30.2"' "$out/debug.json" >/dev/null
jq -e 'length == 0' "$out/collections.json" >/dev/null
for ((i=1; i<=3; i++)); do
  curl -fsS -H 'X-TYPESENSE-API-KEY: repro-key' "$base/metrics.json" > "$out/metrics-$i.json"
done
jq -s 'map({typesense_memory_active_bytes, typesense_memory_resident_bytes,
  typesense_memory_allocated_bytes, typesense_memory_metadata_bytes})' \
  "$out"/metrics-{1,2,3}.json > "$out/metrics-selected.json"
# Equality is the runtime symptom; the source assignment establishes the cause.
jq -e 'length == 3 and all(.[];
  .typesense_memory_active_bytes == .typesense_memory_resident_bytes and
  (.typesense_memory_metadata_bytes | tonumber) > 0)' "$out/metrics-selected.json" >/dev/null
docker exec "$container" uname -srmo > "$out/kernel.txt"
docker image inspect "$image" --format '{{json .RepoDigests}} {{.Architecture}}' > "$out/image.txt"
cat "$out/debug.json" "$out/metrics-selected.json"
printf '\nResults saved in %s\n' "$out"

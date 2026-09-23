#!/usr/bin/env bash
# Requires Docker, curl and jq. Synthetic data; no external service or API key.
set -euo pipefail
image='typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110'
out=${1:-$(mktemp -d "${TMPDIR:-/tmp}/typesense-facet-results.XXXXXX")}
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
api() { curl -fsS --max-time 30 -H 'X-TYPESENSE-API-KEY: repro-key' "$@"; }
api "$base/debug" > "$out/debug.json"
jq -e '.version == "30.2"' "$out/debug.json" >/dev/null
docker exec "$container" uname -srmo > "$out/kernel.txt"
docker image inspect "$image" --format '{{json .RepoDigests}} {{.Architecture}}' > "$out/image.txt"
for variant in int32_no_sort int32_sort int64_no_sort; do
  type=int32
  sortable=false
  if [ "$variant" = int32_sort ]; then sortable=true; fi
  if [ "$variant" = int64_no_sort ]; then type=int64; fi
  jq -n --arg name "$variant" --arg type "$type" --argjson sort "$sortable" \
    '{name:$name, fields:[{name:"experience_min",type:$type,facet:true,sort:$sort}]}' \
    > "$out/$variant-schema.json"
  api -X POST -H 'Content-Type: application/json' \
    --data-binary "@$out/$variant-schema.json" "$base/collections" > "$out/$variant-created.json"
  # Separate writes make insertion order deterministic. Do not bulk import.
  api -X POST -H 'Content-Type: application/json' \
    -d '{"id":"negative","experience_min":-1}' \
    "$base/collections/$variant/documents" > "$out/$variant-negative.json"
  api -X POST -H 'Content-Type: application/json' \
    -d '{"id":"positive","experience_min":1}' \
    "$base/collections/$variant/documents" > "$out/$variant-positive.json"
  for strategy in automatic exhaustive top_values; do
    for scope in positive both; do
      filter='experience_min:>=0'
      if [ "$scope" = both ]; then filter='experience_min:>=-1'; fi
      api --get "$base/collections/$variant/documents/search" \
        --data-urlencode 'q=*' --data-urlencode 'facet_by=experience_min' \
        --data-urlencode "filter_by=$filter" --data-urlencode "facet_strategy=$strategy" \
        --data-urlencode 'per_page=10' --data-urlencode 'max_facet_values=10' \
        > "$out/$variant-$strategy-$scope.json"
    done
  done
done
jq -n '[inputs | {found, hits:[.hits[].document], facets:.facet_counts}]' \
  "$out/int32_no_sort-automatic-positive.json" \
  "$out/int32_no_sort-exhaustive-positive.json" \
  "$out/int32_no_sort-top_values-positive.json" \
  "$out/int32_no_sort-exhaustive-both.json" > "$out/selected-results.json"
# Verify the reported failure and comparison cases, not just HTTP success.
for variant in int32_no_sort int32_sort; do
  for strategy in automatic exhaustive; do
    jq -e '.found == 1 and .hits[0].document.experience_min == 1 and
      .facet_counts[0].sampled == false and .search_cutoff == false and
      .facet_counts[0].counts == [{count:1,highlighted:"-1",value:"-1"}]' \
      "$out/$variant-$strategy-positive.json" >/dev/null
  done
done
for variant in int32_no_sort int32_sort int64_no_sort; do
  jq -e '.facet_counts[0].counts == [{count:1,highlighted:"1",value:"1"}]' \
    "$out/$variant-top_values-positive.json" >/dev/null
done
for strategy in automatic exhaustive; do
  jq -e '.facet_counts[0].counts == [{count:1,highlighted:"1",value:"1"}]' \
    "$out/int64_no_sort-$strategy-positive.json" >/dev/null
done
cat "$out/debug.json" "$out/selected-results.json"
printf '\nReproduced on 30.2; comparison cases passed. Results: %s\n' "$out"

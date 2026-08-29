#!/usr/bin/env bash
set -euo pipefail

probe_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
network_name="jobseek-lightpanda-probe"
fixture_container="jobseek-lightpanda-fixture"
browser_container="jobseek-lightpanda-browser"
crash_container="jobseek-lightpanda-crash"
fixture_image="jobseek-lightpanda-fixture:test"
probe_image="jobseek-lightpanda-runner:test"
browser_image="lightpanda/browser@sha256:bf2328538effa8392166d0cbdba9943a2c97fd19cd2e75b88c8c6f0cf03a1beb"
expected_index="sha256:bf2328538effa8392166d0cbdba9943a2c97fd19cd2e75b88c8c6f0cf03a1beb"
expected_amd64="sha256:9ddc7ba5a147f713f883dace7eba3fe045c5e0537fe1d1160ed2fc4ec5359027"
output_dir="$probe_root/out"

cleanup() {
  docker rm -f "$crash_container" "$browser_container" "$fixture_container" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

inspect_json="$(docker buildx imagetools inspect "$browser_image" --format '{{json .}}')"
test "$(jq -r '.manifest.digest' <<<"$inspect_json")" = "$expected_index"
test "$(jq -r '[.manifest.manifests[] | select(.platform.os == "linux" and .platform.architecture == "amd64") | .digest][0]' <<<"$inspect_json")" = "$expected_amd64"

version_output="$(docker run --rm --platform linux/amd64 --entrypoint /bin/lightpanda "$browser_image" version)"
grep -F '0.3.6' <<<"$version_output" >/dev/null
test "$(docker image inspect "$browser_image" --format '{{.Architecture}}')" = amd64

cd "$probe_root"
rm -rf bin out
mkdir -p bin out
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o bin/fixture ./cmd/fixture
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o bin/probe ./cmd/probe
docker build --target fixture --tag "$fixture_image" .
docker build --target probe --tag "$probe_image" .

docker network create --internal "$network_name" >/dev/null
docker run --detach --name "$fixture_container" --network "$network_name" --network-alias fixture "$fixture_image" >/dev/null
docker run --detach --name "$browser_container" --network "$network_name" --network-alias lightpanda "$browser_image" >/dev/null

run_probe() {
  local plan="$1"
  local output="$2"
  shift 2
  docker run --rm \
    --network "$network_name" \
    --user "$(id -u):$(id -g)" \
    --volume "$probe_root/fixtures:/fixtures:ro" \
    --volume "$output_dir:/out" \
    "$probe_image" \
    -plan "/fixtures/plans/$plan.json" \
    -output "/out/$output.json" \
    "$@"
}

ready=false
for attempt in $(seq 1 20); do
  run_probe navigation navigation-1
  if jq -e '.result.success != null' "$output_dir/navigation-1.json" >/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  docker logs "$browser_container"
  docker logs "$fixture_container"
  jq . "$output_dir/navigation-1.json"
  exit 1
fi

for run in 2 3 4 5; do
  run_probe navigation "navigation-$run"
done
for run in 1 2 3 4 5; do
  result="$output_dir/navigation-$run.json"
  jq -e '.result.success != null' "$result" >/dev/null
  jq -e '.cleanup.session_closed == true and .cleanup.targets_before == 1 and .cleanup.targets_after == 1' "$result" >/dev/null
  jq -e '[.ledger[].path] | index("/static/navigation.js") != null and index("/static/dynamic.js") != null' "$result" >/dev/null
done
for run in 2 3 4 5; do
  cmp "$output_dir/navigation-1.json" "$output_dir/navigation-$run.json"
done

run_probe javascript javascript
jq -e '.result.success.evaluations[0].value.payload == "NDI="' "$output_dir/javascript.json" >/dev/null

run_probe unsupported unsupported
jq -e '.request_count == 0 and .ledger == [] and .result.unsupported.capabilities == ["BROWSER_CAPABILITY_FRAMES"]' "$output_dir/unsupported.json" >/dev/null

run_probe tdm-header tdm-header
jq -e '.result.error.error.code == "ERROR_CODE_TDM_RESERVED" and .result.success == null' "$output_dir/tdm-header.json" >/dev/null

run_probe tdm-meta tdm-meta
jq -e '.result.error.error.code == "ERROR_CODE_TDM_RESERVED" and .result.success == null' "$output_dir/tdm-meta.json" >/dev/null

run_probe subresource-tdm subresource-tdm
jq -e '.result.error.error.code == "ERROR_CODE_TDM_RESERVED" and .result.success == null' "$output_dir/subresource-tdm.json" >/dev/null

blocked_before="$(docker logs "$fixture_container" 2>&1 | grep -c 'GET /blocked/script.js' || true)"
run_probe robots-blocked robots-blocked
jq -e '.result.error.error.code == "ERROR_CODE_NAVIGATION" and any(.ledger[]; .path == "/blocked/script.js" and .decision == "blocked" and .reason == "robots_disallowed")' "$output_dir/robots-blocked.json" >/dev/null
blocked_after="$(docker logs "$fixture_container" 2>&1 | grep -c 'GET /blocked/script.js' || true)"
test "$blocked_before" = "$blocked_after"

run_probe request-overflow request-overflow -max-requests 4
jq -e '.result.error.error.code == "ERROR_CODE_RESOURCE_LIMIT" and any(.ledger[]; .reason == "request_limit")' "$output_dir/request-overflow.json" >/dev/null

run_probe byte-overflow byte-overflow -max-response-bytes 1024
jq -e '.result.error.error.code == "ERROR_CODE_RESOURCE_LIMIT" and any(.ledger[]; .reason == "declared_response_byte_limit")' "$output_dir/byte-overflow.json" >/dev/null

run_probe hang timeout
jq -e '.result.error.error.code == "ERROR_CODE_TIMEOUT" and .result.success == null' "$output_dir/timeout.json" >/dev/null

hang_before="$(docker logs "$fixture_container" 2>&1 | grep -c 'GET /hang' || true)"
docker run --detach \
  --name "$crash_container" \
  --network "$network_name" \
  --user "$(id -u):$(id -g)" \
  --volume "$probe_root/fixtures:/fixtures:ro" \
  --volume "$output_dir:/out" \
  "$probe_image" \
  -plan /fixtures/plans/crash.json \
  -output /out/crash.json >/dev/null
for attempt in $(seq 1 40); do
  hang_after="$(docker logs "$fixture_container" 2>&1 | grep -c 'GET /hang' || true)"
  if (( hang_after > hang_before )); then
    break
  fi
  sleep 0.25
done
test "${hang_after:-$hang_before}" -gt "$hang_before"
docker kill "$browser_container" >/dev/null
timeout 15 docker wait "$crash_container" >/dev/null
jq -e '(.result.error.error.code == "ERROR_CODE_SESSION_LOST" or .result.error.error.code == "ERROR_CODE_TARGET_LOST") and .result.success == null and .cleanup.session_closed == true' "$output_dir/crash.json" >/dev/null
docker rm "$crash_container" >/dev/null
docker rm "$browser_container" >/dev/null

docker run --detach --name "$browser_container" --network "$network_name" --network-alias lightpanda "$browser_image" >/dev/null
ready=false
for attempt in $(seq 1 20); do
  run_probe navigation post-crash
  if jq -e '.result.success != null and .cleanup.targets_before == 1 and .cleanup.targets_after == 1' "$output_dir/post-crash.json" >/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
test "$ready" = true

find "$output_dir" -type f -name '*.json' -exec sha256sum {} + | sort > "$output_dir/result-digests.txt"
test -s "$output_dir/result-digests.txt"

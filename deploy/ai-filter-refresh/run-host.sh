#!/usr/bin/env bash
set -euo pipefail
root="${JOBSEEK_NARROWED_BUNDLE:-/usr/local/lib/jobseek-ai-filter-refresh/current}"
image="$(python3 - "$root/manifest.json" <<'PY'
import json,re,sys
image=json.load(open(sys.argv[1]))['runtimeImage']
assert re.fullmatch(r'node:24-alpine@sha256:[a-f0-9]{64}',image)
print(image)
PY
)"
: "${CREDENTIALS_DIRECTORY:?}"
options=(--rm --init --read-only --network host --user 65532:65532
  --cap-drop ALL --security-opt no-new-privileges --cpus 0.5 --memory 512m --pids-limit 64
  --tmpfs /tmp:rw,noexec,nosuid,size=16m,mode=1777
  --mount "type=bind,src=$(readlink -f "$root")/worker.mjs,dst=/app/worker.mjs,readonly"
  --env-file "$CREDENTIALS_DIRECTORY/worker-env" --env NODE_ENV=production)
if [[ $# = 0 ]]; then
  options+=(--name jobseek-narrowed-worker
    --health-cmd 'test -f /tmp/narrowed-worker-ready'
    --health-interval 5s --health-start-period 30s --health-retries 3)
fi
exec /usr/bin/docker run "${options[@]}" "$image" node /app/worker.mjs "$@"

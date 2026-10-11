#!/usr/bin/env bash
set -euo pipefail
[[ $# == 2 && "$1" =~ ^[0-9a-f]{40}$ ]] || { echo "expected exact source revision and output directory" >&2; exit 1; }
revision="$1"
output="$(mkdir -p -- "$2" && cd -- "$2" && pwd -P)"
module="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../go/deploy-tools" && pwd -P)"
for architecture in amd64 arm64; do
  (cd "$module" && GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH="$architecture" \
    go build -trimpath -ldflags "-buildid= -X main.sourceRevision=$revision" \
    -o "$output/crawler-deploy-tools-$architecture" .)
done
printf '%s\n' "$revision" >"$output/crawler-deploy-tools.source"
(cd "$output" && sha256sum crawler-deploy-tools-amd64 crawler-deploy-tools-arm64 \
  >crawler-deploy-tools.sha256)

#!/bin/sh
set -eu

owner=$(/usr/local/bin/go-typesense-exporter --owner)
case "$owner" in
  python)
    exec uv run --no-sync crawler export
    ;;
  go)
    export GO_TYPESENSE_EXPORTER_ENABLE=1
    exec /usr/local/bin/go-typesense-exporter --run
    ;;
  *)
    echo "Unknown Typesense posting exporter owner" >&2
    exit 2
    ;;
esac

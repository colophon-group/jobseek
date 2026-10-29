#!/bin/sh
set -eu

owner=$(/usr/local/bin/go-typesense-exporter --owner)
if [ "$owner" != go ]; then
  echo "Go exporter ownership is required by the native runtime" >&2
  exit 2
fi
export GO_TYPESENSE_EXPORTER_ENABLE=1
exec /usr/local/bin/go-typesense-exporter --run

#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-write}"

if [[ "$mode" != "write" && "$mode" != "--check" ]]; then
  echo "usage: ./generate.sh [--check]" >&2
  exit 2
fi

for tool in uv protoc-gen-go gofmt; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "$tool is required" >&2
    exit 2
  fi
done

if [[ "$(protoc-gen-go --version)" != "protoc-gen-go v1.36.10" ]]; then
  echo "protoc-gen-go v1.36.10 is required" >&2
  exit 2
fi
if [[ "$(uv run --project "$root/../.." python -m grpc_tools.protoc --version)" != "libprotoc 31.1" ]]; then
  echo "grpcio-tools 1.76.0 (libprotoc 31.1) is required" >&2
  exit 2
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/python" "$tmp/go"

uv run --project "$root/../.." python -m grpc_tools.protoc \
  --proto_path="$root" \
  --python_out="$tmp/python" \
  --go_out="$tmp/go" \
  --go_opt=paths=source_relative \
  "$root/runtime.proto"

PYTHONPATH="$root" uv run --project "$root/../.." python \
  "$root/tools/generate_limits.py" --go-out "$tmp/limits_gen.go"
gofmt -w "$tmp/limits_gen.go"

if [[ "$mode" == "--check" ]]; then
  diff -u "$root/gen/python/runtime_pb2.py" "$tmp/python/runtime_pb2.py"
  diff -u "$root/gen/go/runtime.pb.go" "$tmp/go/runtime.pb.go"
  diff -u "$root/conformance/go/limits_gen.go" "$tmp/limits_gen.go"
  exit 0
fi

mkdir -p "$root/gen/python" "$root/gen/go"
cp "$tmp/python/runtime_pb2.py" "$root/gen/python/runtime_pb2.py"
cp "$tmp/go/runtime.pb.go" "$root/gen/go/runtime.pb.go"
cp "$tmp/limits_gen.go" "$root/conformance/go/limits_gen.go"

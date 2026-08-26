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
PYTHONPATH="$root" uv run --project "$root/../.." python \
  "$root/tools/generate_privacy.py" \
  --python-out "$tmp/privacy_rules.py" --go-out "$tmp/privacy_gen.go"
PYTHONPATH="$root" uv run --project "$root/../.." python \
  "$root/tools/generate_extensions.py" \
  --python-out "$tmp/extension_rules.py" --go-out "$tmp/extensions_gen.go"
gofmt -w "$tmp/limits_gen.go" "$tmp/privacy_gen.go" "$tmp/extensions_gen.go"

if [[ "$mode" == "--check" ]]; then
  diff -u "$root/gen/python/runtime_pb2.py" "$tmp/python/runtime_pb2.py"
  diff -u "$root/../../src/runtime_contract/v1/runtime_pb2.py" "$tmp/python/runtime_pb2.py"
  diff -u "$root/gen/go/runtime.pb.go" "$tmp/go/runtime.pb.go"
  diff -u "$root/conformance/go/limits_gen.go" "$tmp/limits_gen.go"
  diff -u "$root/conformance/go/privacy_gen.go" "$tmp/privacy_gen.go"
  diff -u "$root/conformance/go/extensions_gen.go" "$tmp/extensions_gen.go"
  diff -u "$root/conformance/python/privacy_gen.py" "$tmp/privacy_rules.py"
  diff -u "$root/conformance/python/extension_rules.py" "$tmp/extension_rules.py"
  diff -u "$root/../../src/runtime_contract/v1/privacy_rules.py" "$tmp/privacy_rules.py"
  diff -u "$root/../../src/runtime_contract/v1/extension_rules.py" "$tmp/extension_rules.py"
  PYTHONPATH="$root" uv run --project "$root/../.." python "$root/tools/check_proto_compat.py"
  exit 0
fi

mkdir -p "$root/gen/python" "$root/gen/go"
cp "$tmp/python/runtime_pb2.py" "$root/gen/python/runtime_pb2.py"
cp "$tmp/python/runtime_pb2.py" "$root/../../src/runtime_contract/v1/runtime_pb2.py"
cp "$tmp/go/runtime.pb.go" "$root/gen/go/runtime.pb.go"
cp "$tmp/limits_gen.go" "$root/conformance/go/limits_gen.go"
cp "$tmp/privacy_gen.go" "$root/conformance/go/privacy_gen.go"
cp "$tmp/extensions_gen.go" "$root/conformance/go/extensions_gen.go"
cp "$tmp/privacy_rules.py" "$root/conformance/python/privacy_gen.py"
cp "$tmp/extension_rules.py" "$root/conformance/python/extension_rules.py"
cp "$tmp/privacy_rules.py" "$root/../../src/runtime_contract/v1/privacy_rules.py"
cp "$tmp/extension_rules.py" "$root/../../src/runtime_contract/v1/extension_rules.py"

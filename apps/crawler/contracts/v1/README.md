# Crawler runtime contract v1

This directory is the authoritative language-neutral boundary for crawler
runtime execution. [`runtime.proto`](runtime.proto) is the sole v1 wire IDL.
The checked-in Python and Go bindings are generated from it; protobuf-JSON is
used only for readable shared fixtures, while production framing is
length-delimited protobuf.

The provisional JSON Schemas previously in this directory were removed when
v1 was promoted. Storage rows, Redis task hashes, CSV, `ws`, and Murmur are not
wire representations of this contract. They must be normalized into
`BoardManifest` by the crawler-owned catalog/runtime adapter work.

## Layout

- `runtime.proto` — authoritative messages, enums, and tagged unions.
- `gen/{python,go}` — committed generated reference bindings.
- `python/crawler_runtime_contracts` and `framing/` — installable Python and Go
  package boundaries, including the reference length-delimited codecs.
- `conformance/{python,go}` — independent semantic validators using the same
  fixtures.
- `limits.json`, `extension_registry.json`, and `privacy_registry.json` —
  single sources for generated hard limits, extension schema/context routing,
  and replay secret-name/header registries in both languages.
- `fixtures/conformance` — positive and negative state-machine cases.
- `fixtures/replay` — bounded, deterministically redacted offline captures.
- `protocol.md` — framing, state, origin identity, and authority rules.
- `error-taxonomy.md` — closed error-to-policy mapping.
- `replay.md` — corpus, canonicalization, redaction, and projection rules.
- `compatibility.md` and `converters/` — version/evolution policy.
- `metrics.md` — bounded replacement-boundary metric definitions.

## Generate and verify

From `apps/crawler/contracts/v1`:

```bash
# Python compiler comes from pinned grpcio-tools 1.76.0 (libprotoc 31.1).
# The Go plugin must be exactly v1.36.10.
go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.10
./generate.sh
./generate.sh --check

PYTHONPATH=python:. uv run --project ../.. pytest -q conformance/python/test_contract.py
go test ./...
PYTHONPATH=python:. uv run --project ../.. python tools/check_contract.py
```

The Python binding is generated for protobuf 6.31.1 and is runtime-tested
against the crawler's `protobuf>=6.33,<7` dependency. Local system `protoc`
versions are deliberately ignored. The crawler wheel and runtime image expose
`crawler_runtime_contracts.v1`; importing from the repository-only `gen`
directory is unsupported.

This foundation owns core messages and representative safety fixtures.
Exhaustive provider-family extraction and Lightpanda capability corpora remain
in #7953–#7963; adding those here would turn the stable contract gate into a
provider implementation suite.

No free-form protobuf `Struct` or `Value` exists in the request, output, or
policy surface. Common fields are typed. Provider-specific semantics use a
bounded `ExtensionEnvelope` whose schema ID/version/encoding must be present in
the fail-closed Python/Go registry generated from `extension_registry.json`.

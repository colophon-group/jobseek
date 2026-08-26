# Versioning and compatibility

The committed `compatibility_baseline.json` freezes the exact deterministic v1
file descriptor. `tools/check_contract.py` compares the generated descriptor
hash on every run, so even an otherwise protobuf-compatible additive v1 field
requires a deliberate v2 contract. Consumers must still preserve unknown
protobuf fields while forwarding/storing. Field numbers and meanings are never
reused. Required semantic behavior is not added through an optional field.
Enum zero remains `UNSPECIFIED`.

Unknown enum numerics survive protobuf decode for forwarding, but a v1 runtime
validator rejects them before policy or commit-eligible state changes. A newer
optional diagnostic enum may be ignored only where this document explicitly
allows it; error, outcome, capability, kind, terminal, and origin enums never
default into behavior.

Any wire change, removed/renamed field, changed meaning, tightened
previously-valid behavior, changed hash/canonicalization rule, or new required
semantic creates `contracts/v2`. The same PR must add deterministic converters
in both Python and Go under `converters/v1_to_v2`, a machine-readable converter
manifest, round-trip and lossy/error fixtures, and a deployment plan proving
the pinned rollback artifact can still read/write the live shape.
`tools/check_contract.py` requires concrete `converter.json`, `python.py`,
`converter.go`, `fixtures/roundtrip.json`, and `fixtures/lossy.json`; an empty
directory or empty placeholder file does not satisfy the gate. The manifest
must identify the exact source/target contract and bidirectional
upgrade/downgrade support; both fixture documents are nonempty vector lists,
the Python converter must compile, and the Go converter must declare a package.

There is no converter for the provisional JSON Schemas: they were temporary,
had no Go binding, and had no production consumer. The existing in-process
Python runtime seams remain internal adapters, not v1 wire messages.

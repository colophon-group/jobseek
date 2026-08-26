# Candidate runtime v1 compatibility

`runtime.proto` is the sole candidate wire IDL for `crawler.runtime/v1`. It is
not a production activation: lane 6 is the only lane allowed to generate and
package bindings or switch a runtime consumer to this contract.

## Frozen introduction

`baseline/runtime-v1.descriptor.b64` is the canonical base64 encoding of one
`google.protobuf.FileDescriptorSet`, generated without source information. Its
manifest records the exact source hash, descriptor hash, compiler, and
pre-introduction `main` commit. There is deliberately no baseline-writing mode.

The checker compiles into two fresh temporary directories and requires
byte-identical descriptors. On the introduction change the source, manifest,
and descriptor must agree exactly. After introduction, the checker reads the
descriptor, manifest, and `runtime.proto` from authenticated prior main. It
rejects any local baseline rewrite before comparing both the introduction and
prior-main descriptors with the current descriptor. Regenerating files
together cannot conceal a break, and later additive symbols stay protected.

Within v1, optional additive fields, enum values, messages, and declarations
may be accepted only when they do not impose new required behavior. Existing
field numbers and meanings are never reused. The gate rejects changes to:

- package, syntax, file/message/enum names, and relevant descriptor options;
- field name, number, type, type name, cardinality, presence, `json_name`,
  oneof membership/declaration, default, extendee, or options;
- enum value name, number, or options; and
- removal of an existing message, field, enum, oneof, or enum value.

A removed field or enum value must reserve both its name and number in the new
major version. Reservations prevent reuse; they do not make the removal legal
inside v1. The mutation fixture exercises every listed break, both unreserved
and correctly reserved removals, while a combined additive mutation passes.
An adjacent version must retain a tombstone message or enum when needed to
carry those reservations; silently dropping the whole declaration fails.

Unknown protobuf fields and enum numerics must survive forwarding and storage.
An authoritative validator must reject an unknown behavior-driving enum before
dispatch or a commit-eligible state change. Every enum keeps zero as
`UNSPECIFIED`.

`CanonicalizationRule` and `HashRule` are closed wire identifiers. Their v1
values name the content/projection canonicalization and content/semantic digest
rules that lane 5 will implement. Changing either rule is a v2 semantic change,
not an additive v1 adjustment.

## Adjacent versions

Any removed or renamed field, changed meaning, tightened previously valid
behavior, changed hash/canonicalization rule, or new required semantic creates
`contracts/v2`. The same change must add
`v1/converters/v1_to_v2/{converter.py,converter.go,converter.json}` and the
shared nonempty `fixtures/{roundtrip,lossy}.json` files described in
`converters/README.md`.

The compatibility gate compiles the Python program, compiles and runs the Go
program, and executes every nonidentity round-trip and genuinely lossy vector
in both languages. Both outputs must equal the shared expected JSON. It does
not infer implementation from source text and does not accept placeholder
regex checks.

## Provisional-schema consumer audit

At introduction base
`47b108c5e974f9bdb50aac69519755c2bfc2584a`, this exact production-only search
returned no matches:

```sh
git grep -n -E '(contracts/v1|runtime[.]proto|runtime_pb2|runtimev1|[.]schema[.]json)' \
  -- apps/crawler/src apps/web scripts
```

The legacy in-process Python seams emit the string `crawler.runtime/v1`, but
they do not locate, parse, import, or generate from the provisional JSON
schemas or this protobuf IDL. Tests and contract documentation were excluded
from the production-consumer claim. The checked audit record is
`fixtures/compatibility/production-consumer-audit.json`.

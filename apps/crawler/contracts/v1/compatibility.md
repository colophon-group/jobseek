# Candidate runtime v1 compatibility

`runtime.proto` is the sole candidate protobuf IDL for
`crawler.runtime/v1`. It is not packaged or consumed by production, and only
lane 6 may activate it after the remaining conformance lanes land.

The existing JSON schemas and in-process Python adapters also use the string
`crawler.runtime/v1`; those provisional JSON surfaces are live in tests and
Python boundary code. The narrower statement here is that this protobuf file
has no generated bindings, package entry, loader, or production consumer yet.
Freezing the protobuf does not silently promote or retire the JSON surfaces.

## Frozen introduction

`baseline/runtime-v1.descriptor.b64` is one canonical base64-encoded
`google.protobuf.FileDescriptorSet`. Its manifest records the exact source
hash, descriptor hash, compiler, and pre-introduction `main` commit
`7c8556642b32ac78871cd015931b95d968e83e7d`. No baseline-generation or skip
mode is exposed by the checker.

The gate authenticates prior main from immutable GitHub event provenance in CI
and from the local `origin/main` ancestry elsewhere. On introduction, the
committed source hash and structural descriptor must match the frozen baseline.
After introduction, both the baseline files and manifest must remain byte-for-
byte equal to prior main, and the current descriptor is compared with both the
introduction baseline and the latest prior-main source. This prevents a change
from hiding a break by regenerating the baseline and also protects fields that
were added after the initial freeze.

Different supported `protoc` releases may serialize equivalent descriptor
metadata differently. The baseline bytes and digest remain immutable, while
compatibility is decided by parsed structural shape. Two compilations by the
active compiler must still be byte-identical.

## Structural rules

Optional additive fields, enum values, messages, and oneof declarations are
compatible only when they do not impose new required behavior. Existing field
numbers and meanings are never reused. The gate rejects changes to:

- file name, package, syntax, and relevant file/message/enum options;
- message and enum identity;
- field name, number, type, type name, cardinality, presence, `json_name`,
  oneof membership/declaration, default, extendee, or options; and
- enum value name, number, or options.

Removing a field or enum value must reserve both its name and number in the new
major version. Those reservations prevent reuse; they do not make the removal
legal inside v1. Reservations already present in prior main are themselves
immutable: the gate rejects removing or narrowing a reserved range or name and
rejects reusing either one. Protobuf maps are checked as their generated entry
messages, so removal, field-number, key-type, value-type, and `map_entry`
changes are breaking. Unknown wire fields and enum numerics must survive
forwarding, but an authoritative validator must reject unknown behavior-driving
values before origin dispatch or a commit-eligible state change.

`CanonicalizationRule` and `HashRule` are closed wire identifiers. Lanes 3-5
will implement the frozen identity, replay, privacy, projection, and hashing
semantics without changing this IDL.

## Cross-lane frozen surface

After this descriptor lands, lanes 3-5 add validators and shared corpora; they
do not edit `runtime.proto`. A missing field is a compatibility defect to route
back through an explicit reviewed IDL amendment, not an implicit lane-local
addition.

| Lane | Frozen messages | Frozen enums |
| --- | --- | --- |
| 3: replay/control identity and state | `ExecutionRequest`, `FencingContext`, `OriginOperationRef`, `OriginOperationDeclared`, `ResumeRequest`, `OriginContact`, `ExecutionFrame`, `Terminal`, `DisconnectFault`, `ProtocolEvent`, `ProtocolTranscript` | `ExecutionKind`, `TerminalStatus`, `ErrorCode`, `ErrorDisposition`, `OriginContactDisposition`, `EventDirection`, `DisconnectPoint` |
| 4: bounded capture/redaction | `Header`, `ArtifactHandle`, `DataChunk`, `ChunkManifest`, `CapturedRequest`, `CapturedResponse`, `CapturedExchange`, `ExtensionEnvelope` | `CaptureKind`, `ExtensionEncoding` |
| 5: content/projection/hash | `JobContent`, `MonitorResult`, `ScrapeResult`, `JobEffect`, `ProjectedTarget`, `ProjectedEffects`, `ReplayCase` | `ReplayAdapter`, `ProjectedAction`, `CanonicalizationRule`, `HashRule` |

## Required CI enforcement

`tests/test_runtime_v1_compatibility.py` is a narrow discovery bridge for the
full conformance suite. It fail-closed discovers every Python conformance
module and every Go `*_test.go` package below `contracts/v1`, rejects empty or
hidden Python suites, uncollectable `Test*` classes, and symlink/package escapes,
and runs each Go package with bounded `go test -race -count=1 -json` plus
`go vet`. Missing compilers, empty Go packages, timeouts, excessive output,
panics, and nonzero exits fail normal test discovery. Therefore the ordinary
Required CI command, `pytest tests/`, runs the structural mutation matrix,
current-source check, committed self-regeneration regression, post-introduction
reservation and map-history regressions, shared Python corpora, and shared Go
corpora.

Adjacent-version reservation and executable converter policy is intentionally
out of scope here and is owned by #8057. Generated bindings, packaging,
semantic validators, replay/redaction/projection implementations, runtime
consumption, and activation are also excluded.

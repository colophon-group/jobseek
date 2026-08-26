# Candidate runtime v1 replay/control conformance

The shared control corpus under `fixtures/control/` is deterministic, offline
evidence for the state semantics in `execution-protocol.md`. It does not contact
an origin, invoke a browser, consume a queue, read production persistence, or
activate the candidate runtime.

## Corpus representation

`manifest.json` is a strict normalized transcript projection. Each case has
exactly:

- a stable `id`;
- ordered `events` projected from actual `ClientMessage`, `ServerMessage`, and
  `DisconnectFault` fields;
- `expected.accepted` and one stable local validator error code; and
- fixture-only `metadata` containing `durable_cut_event_index`,
  `injection_phase`, and one strict RFC3339 `logical_time_rfc3339`.

The projection uses readable enum labels and a fixture payload `type`
discriminator. A separate `measurements` object holds frame wire size and the
monitor output-item count used for accounting; it is never presented as an
`ExecutionFrame` or `ProtocolEvent` field. Actual artifact handle size remains
inside its payload projection. These bounded measurements let both validators
assert accounting and actual limits without embedding large bodies. The corpus
never adds an ACK, ProtocolEvent timestamp,
resume fingerprint/manifest/limit/deadline/trace field,
`max_origin_operations`, `max_errors`, or terminal `error_count`.

`generate.py --write` renders sorted ASCII JSON and `manifest.sha256`.
`generate.py --check` fails on any byte, ordering, or digest drift. Case IDs are
sorted and unique. The required 27 IDs are independently hard-coded in the
generator, Python validator/test, and Go validator/test; missing, renamed,
duplicate, unknown-required, or skipped required cases fail closed. Additional cases may
exercise distinct edge conditions but cannot substitute for that required set.

## Coverage

Positive cases cover the initial handshake, complete execution, bounded window
replenishment, every actual `ExecutionFrame` oneof arm as an identical
unacknowledged replay, every actual disconnect point, result-before-terminal,
H0 to H1/H2 resume, repeated resume, dynamic declaration, ambiguous dispatch,
and exactly-once deduplication. Every resume has a fresh Hello/ServerHello pair;
the sole missing-handshake transcript is an explicit negative case.

Negative cases cover immutable request/manifest/deadline/trace/limit binding,
unknown checkpoints, reused and stale attempts, stale fences, cancellation and
deadline history, unknown or undeclared origins, identity/fingerprint mutation,
duplicate dispatch/deduplication, false fault metadata, divergent replay,
sequence rewind/gap, physical-credit exhaustion, actual observable limits,
local non-wire origin/error safety caps, artifact-handle identity, ACK
high-water rewind, initial origin ordering/parents, durable cut/phase mismatch,
stale old-attempt traffic after disconnect, cancel-bound terminal status, and every
actual terminal counter/eligibility/status condition.

Sequences in fixtures are zero-based. `after_sequence` is the sole ACK. Logical
nonterminal frames count once even when replayed; every physical transmission
spends credit. Terminal occupies a sequence but is excluded from
`max_execution_frames` and `Terminal.frame_count`.

## Validators and stable results

Run the Python checker from the repository root:

```text
python3 apps/crawler/contracts/v1/tools/check_protocol.py \
  --root apps/crawler/contracts/v1
```

`--json` emits the stable result for every case: case ID, accepted/code,
fixture-binding SHA-256, logical/physical diagnostic counts, ledger state, and
actual terminal fields. The local error registry is closed and fully exercised
by the corpus. It does not modify `runtime.proto`'s `ErrorCode` enum.

The standard-library Go validator consumes the same bytes and returns exactly
the same result objects. Its tests execute the Python checker and compare the
entire result list, then run under the race detector. Both suites verify fixture
determinism and prevent network imports/access; ordinary Required CI discovers
`conformance/python/test_state.py` and `conformance/go/state_test.go`
dynamically.

The result is candidate-only. Even an accepted terminal with
`eligible_for_commit=true` grants no runtime, persistence, scheduling,
deployment, `ws`, Murmur, or MCP authority. Lane 6 must review the complete
contract stack and explicitly activate it before any production consumer may
use this protocol.

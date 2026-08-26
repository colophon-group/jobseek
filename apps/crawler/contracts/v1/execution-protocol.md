# Candidate crawler runtime v1 execution protocol

This document specifies the candidate control-state semantics of the frozen
`crawler.runtime/v1` descriptor. It is conformance evidence, not an activated
runtime. Only the later activation lane may generate bindings, package a
consumer, select a deployment, or grant persistence authority. In particular,
`eligible_for_commit` is a protocol completeness result; it never authorizes a
DB, Redis, queue, `ws`, Murmur, MCP, or crawler-state mutation.

`runtime.proto` is the sole candidate wire schema. A candidate session carries
protobuf `ClientMessage` and `ServerMessage` records using the bounded raw
record primitive in `framing.md`. This lane does not change or re-specify that
framing primitive.

## Session and immutable binding

An initial session has three ordered messages:

1. `ClientHello` advertises `crawler.runtime/v1`, the client implementation,
   and requested limits.
2. `ServerHello` selects that version, accepts limits no larger than requested,
   supplies a positive initial frame window no larger than
   `max_in_flight_frames`, confirms origin-ID resume, and records its strict
   RFC3339 UTC acceptance time.
3. `ExecutionRequest` starts one monitor, scrape, or browser execution.

The owner persists a deterministic binding before accepting a result frame.
The binding covers the state corpus's actual request projection with
`attempt_id` removed plus the accepted `Limits`: request and root-origin IDs,
execution kind and input, board-manifest identity/revision fields, deadline,
trace context, initial origin identity, and fencing context. Dynamic
declarations extend the stored ledger under that binding. `attempt_id` is
deliberately excluded because it names a
transport attempt rather than semantic work. The conformance result exposes a
SHA-256 of this fixture binding only; canonical wire/hash rules remain lane 5
work.

A second `ExecutionRequest` for already stored state is never a new execution.
The validator classifies a changed manifest revision, regressed deadline,
changed trace context, or stale fence before the generic `binding_changed`
failure. An otherwise identical second start still fails because resume must
use `ResumeRequest`.

The request deadline must be strict RFC3339, later than `ServerHello` acceptance,
and no farther away than accepted `max_active_duration_ms`. `traceparent` uses
W3C version 00 shape with nonzero trace/span IDs. `tracestate` requires unique,
bounded members and cannot exist without `traceparent`. Both remain immutable
binding inputs; neither is repeated on resume.

## Resume and acknowledgement

A reconnect repeats `ClientHello` and `ServerHello`, then sends exactly the
fields present in `ResumeRequest`: contract version, request ID, root origin ID,
a new attempt ID, optional `after_sequence`, and fencing context. Accepted
limits must equal the persisted limits. The request/operation identity and
fence must resolve the stored execution, and an attempt ID may never be reused.
No request fingerprint, manifest, deadline, trace context, or limit is invented
on the resume message.

Every resume requires a fresh, ordered Hello pair. Consuming a start or resume
also consumes that negotiation; an older attempt's accepted limits/window
cannot be reused for another attempt.

`after_sequence` is the only acknowledgement. There is no ACK event. Sequence
numbers are zero-based:

- omission acknowledges no frame;
- a stored sequence acknowledges that sequence and everything before it;
- a value below the last durable sequence requires physical replay from the
  next sequence through that last sequence;
- the last durable sequence permits the next new logical frame; and
- an unseen sequence fails `unknown_checkpoint`.

The owner also persists the greatest acknowledged sequence. A later resume may
advance or repeat that high-water mark but cannot lower it by supplying an
older value or omitting `after_sequence`.

Every replayed frame is sent under the current resumed `attempt_id`. Replay
equality excludes only `attempt_id`: contract/request IDs, zero-based sequence,
fence digest, payload, logical meaning, and bounded wire size remain identical.
A replay neither reapplies state nor increments logical counts. A divergent
reuse fails closed.

## Ordered frames, credit, and limits

New logical frames are contiguous from sequence zero. A rewind, gap, frame
after terminal, or second terminal is illegal. Each physical frame, including
an identical replay, spends one unit of the active attempt's frame window.
`WindowUpdate` may replenish only the current request/attempt/fence and may not
raise available credit above `max_in_flight_frames`. A resumed attempt starts
with the newly accepted initial window.

The negotiated `Limits` allowlist is exactly the 16 fields in the frozen
descriptor:

- `max_frame_bytes`, `max_inline_body_bytes`, `max_artifact_chunk_bytes`;
- `max_monitor_batches`, `max_output_items`, `max_in_flight_frames`;
- `max_active_duration_ms`;
- `max_browser_actions`, `max_browser_captures`,
  `max_browser_evaluations`;
- `max_http_transfer_bytes`, `max_browser_transfer_bytes`;
- `max_execution_frames`, `max_artifact_count`,
  `max_artifact_total_bytes`; and
- `max_retry_after_ms`.

The state corpus enforces fields for which its transcript projection contains
wire evidence: frame bytes, physical credit, unique nonterminal execution
frames, monitor batches, output items, unique artifact handles and total
artifact bytes, and active duration. Negotiation validates every limit name and
that accepted values do not exceed requested values. It does not claim evidence
for HTTP/browser transfer totals, browser-plan sub-counts, retry-after, inline
body, or artifact-chunk sizes when the fixture contains no corresponding wire
content. Lane 4 owns decoded body/chunk validation.

`max_execution_frames` counts unique nonterminal logical frames. Terminal is
sent at the next sequence but is excluded from that limit and from
`Terminal.frame_count`. Physical replay still consumes credit but never
increases either count. Error frames and origin declarations have no dedicated
negotiated limit in v1; the conformance machine applies explicit local safety
caps in addition to frame/byte limits. Those caps are not wire fields.

## Origin-operation ledger and disconnect history

The ledger starts with ordered `ExecutionRequest.origin_operations`. Its first
entry equals the request's root `origin_request_id`. A dynamic
`OriginOperationDeclared` allocates the next sequence and immutable,
fully-qualified ID before dispatch; identity reuse, mutation, an unknown parent,
or contact without declaration fails closed.

The descriptor proves only these states:

- `declared`: identity is durable and no dispatch frame exists;
- `dispatched`: a `DISPATCHED` contact frame exists;
- `ambiguous`: a matching disconnect follows durable dispatch history; and
- `deduplicated`: after resume, exactly one logical `DEDUPLICATED` contact
  resolves that ambiguous operation.

An ambiguous operation cannot be blindly dispatched again. Replaying its
original unacknowledged `DISPATCHED` frame restores physical history without a
second logical dispatch. A subsequent `DEDUPLICATED` contact applies once;
replaying it does not reapply it. Monitor, scrape, and browser results are
request-scoped because their messages carry no origin-operation ID. The
validator therefore does not invent completion states or attribute results to
individual operations.

`DisconnectFault` is accepted only when its point, optional sequence,
`origin_was_dispatched`, origin ID, fingerprint, and surrounding durable frame
history agree. The four actual points are `AFTER_DISPATCH`, `BEFORE_FRAME`,
`AFTER_FRAME`, and `RESULT_BEFORE_TERMINAL`; there is no `BEFORE_DISPATCH`
value. Cancellation and deadline expiry are history transitions, never
disconnect fault types. Fault provenance follows the last accepted physical
frame, which may be a replay, rather than only the last logical frame. An
omitted fault sequence is legal when surrounding physical history still proves
the point.

Accepting any disconnect fault invalidates the active transport attempt. No
old-attempt frame, window update, cancellation, second fault, or start is legal
afterward. Only a fresh `ClientHello`, `ServerHello`, then valid `ResumeRequest`
installs a new attempt and permits result traffic again.

Case metadata supplies only the deterministic durable cut index, injection
phase, and logical time and is not encoded as wire data. Cut and phase must
match the transcript. Durable prefix events through the cut are restored before
the case logical time is applied to the post-cut transition.

## Result and terminal accounting

Logical accounting is deterministic:

- monitor output items are the items represented by each unique monitor batch;
- a unique scrape or browser result contributes exactly one output item;
- `monitor_batches` counts unique monitor-result frames;
- `artifact_count` counts unique logical artifact handles, and repeated use of
  a handle by another logical frame is rejected;
- `origin_operation_count` is the number of unique declarations, including
  initial request operations; and
- diagnostic error count has no `Terminal` field and is never invented there.

Replay does not increment any of these values. Exactly one terminal must be
logically last. Its actual counters must equal the logical state, and
`active_duration_ms` must fit the negotiated ceiling. Success is commit-eligible
only when the request-scoped result is complete, no error exists, and no origin
operation remains ambiguous. Error requires a prior error frame and is not
eligible. After a matching cancel, only one `CANCELLED`, non-eligible terminal
with correct logical counts may close the transcript; every other later frame
is stale. A cancelled terminal without cancellation history is illegal.

These rules prove protocol completeness only. The authoritative owner must
still revalidate current lease, claim token, and routing epoch before any
persistence action.

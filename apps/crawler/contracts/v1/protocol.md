# Runtime protocol v1

## Transport and framing

Peers exchange protobuf `ClientMessage` and `ServerMessage` records using an
unsigned-varint byte length followed by exactly that many protobuf bytes. The
length prefix and payload together may not exceed negotiated
`max_frame_bytes`. EOF within either is an ambiguous disconnect, never a
terminal result. Unix sockets, pipes, and authenticated TCP may carry the same
framing; v1 does not require gRPC or expose raw CDP.

The bound uses the exact unsigned-varint prefix length, not a ten-byte
reservation. Both bindings reject truncated/overlong/overflow prefixes,
truncated payloads, trailing bytes, and records whose prefix plus payload
exceeds the cap.

The first records are `ClientHello`, then `ServerHello`. The server selects
`crawler.runtime/v1`, sets each accepted limit no higher than the client's
request or the hard v1 ceiling, and grants finite initial frame credit. Each
server execution frame consumes one credit. `WindowUpdate` replenishes credit
without allowing outstanding credit above `max_in_flight_frames`. A producer
that has no credit must stop reading/producing upstream output rather than
buffer an unbounded board.

## Semantic origin identity

An execution may contain multiple origin operations: initial navigation,
pagination, an action-triggered request, or a detail fetch. Every operation
has its own stable `origin_request_id`, contiguous `operation_sequence`, role,
and optional earlier parent. The request-level `origin_request_id` equals the
first operation ID. Every v1 operation is carried on `ExecutionRequest` with a
SHA-256 request fingerprint over the canonical method, URL, headers, and body.
The entire ID/parent/role/sequence/fingerprint binding is durable before the
request is accepted. v1 fails closed on `OriginOperationDeclared`; it has no
dynamic-origin execution mode.

The fingerprint is
`SHA256("crawler.runtime/v1/request" || NUL || deterministic protobuf)` over
the bounded `CapturedRequest` shape after deterministic redaction, with headers
sorted by canonical name. This binds retries without persisting raw secrets.

`attempt_id` identifies one transport connection only. A retry retains the
request ID and every operation ID. Before contacting an origin, an extractor
atomically records the operation as dispatched. `OriginContact(DISPATCHED)` is
legal once per operation. After reconnect, it returns cached/replayed output
and `DEDUPLICATED`; it does not dispatch again. If durable state cannot prove
whether an operation dispatched, `ResumeRejected(AMBIGUOUS_ORIGIN,
FAIL_CLOSED_POLICY)` returns control to normal scheduler/politeness policy.
The caller must not invent a new ID or blindly repeat live traffic.
After an `AFTER_DISPATCH` disconnect, the first new semantic frame on a
successful resume must be the `DEDUPLICATED` contact for that exact operation.
The contact must retain the pre-dispatch fingerprint. Deduplication is illegal
before resume and may occur at most once for that ambiguous dispatch.

Replay capture is therefore one ordered `CapturedExchange` per semantic
origin operation, not one exchange per execution. This covers paginated and
session/action flows without conflating their independent at-most-once keys.

## Execution state machine

1. Hello/hello selects v1 and finite limits/credit.
2. One `ExecutionRequest` starts monitor, scrape, or correlated browser work.
3. Frames use the same `request_id`, current transport `attempt_id`, mandatory
   32-byte fencing digest, and contiguous sequence numbers from zero.
4. Monitor work may emit origin contacts, artifacts, monitor batches, or one
   error. Scrape work substitutes exactly one scrape result for batches.
5. Error is followed only by an error terminal. Cancellation is followed only
   by a cancelled terminal.
6. Exactly one terminal is last. Its frame/output/batch/artifact/origin counts
   exactly match observed state.
7. Only a successful, complete terminal is `eligible_for_commit=true`. This is
   protocol-level semantic eligibility, never persistence authority. Every
   partial result, disconnect, error, cancellation, limit violation,
   unsupported browser capability, stale lease/epoch, or missing terminal is
   ineligible and cannot drive discovery diffs, gone decisions, watermarks,
   failure budgets, rescheduling, or persistence.

On disconnect, a new handshake and `ResumeRequest` may continue after the
last acknowledged sequence. An omitted `after_sequence` acknowledges nothing,
so previously emitted unacknowledged frames may be replayed with identical
semantic content under the new attempt. Replayed acknowledged or changed
frames, gaps, changed semantic IDs, or reused transport attempts are invalid.
Disconnects after dispatch, before/after each representative frame, and after
result before terminal are shared conformance fixtures. The corpus includes a
predeclared operation disconnected after durable dispatch but before its first
contact, then resumed with the same complete identity and a deduplicated
contact. A disconnect after a validated terminal is immaterial
because execution is already complete.

## Normalized results

- URLs are visible ASCII absolute HTTP(S), with lowercase scheme/host, a
  nonempty slash-prefixed path, no credentials, fragment, percent escapes,
  backslashes, default port, host trailing dot, empty/dot path segments, or
  duplicate query pairs. Query pairs use one `=` each and are lexically
  sorted. Sensitive query values are deterministic redaction pseudonyms.
  These deliberately narrow rules are identical in Python and Go.
- Every rich job URL is in `MonitorResult.urls`. Non-hybrid rich output has a
  job for every URL; hybrid output may have a subset.
- Optional scalar/message presence is semantic. `locations` absent means the
  upstream field was missing; a present empty `StringList` means explicit
  empty. Proto3 repeated fields alone are not used where this distinction is
  needed.
- `truncated`, `hybrid`, `filtered_count`, `security_filtered_count`, sitemap
  replacement, and metadata updates participate in comparison and projected
  effects. Counts alone are never parity evidence.
- Deterministic protobuf hashes preserve optional-field presence and sort
  semantically unordered locations, skills, localizations, and extensions.
  Canonical locales use the supported BCP47 subset and legacy aliases reject.
  Rich jobs require meaningful title and description; empty/whitespace and
  sentinel values are never commit-eligible. Projected `JobEffect` records
  bind each content hash to its typed job URL, including scrape source URL.
- Metadata updates remain ordered and lossless: each deterministic protobuf
  update is prefixed by its unsigned 64-bit big-endian length before the
  projection SHA-256 is updated. Repeated fields are never overwritten by a
  later batch.

## Browser boundary

`BrowserPlan` uses closed navigation/session/action/capture/evaluation and
interception messages. Every action/evaluation declares a closed network
effect; origin contact requires a stable declared operation ID and only
explicit no-network work may omit it. The plan travels in `ExecutionRequest`
and the result in `ExecutionFrame`, inheriting deadline, cancellation, fence,
credit, limits, resume, and framing. `BrowserResult` is a protobuf `oneof`:
success, error, or unsupported. Error/unsupported variants can carry only
diagnostic artifact handles, never partial HTML/captures/evaluations. Required
unsupported capabilities therefore reject the entire result by construction
and terminate ineligible for commit. Success must return exactly the plan's
unique action/capture/evaluation IDs, with every action completed. `TARGET_LOST`
and `SESSION_LOST` are distinct typed failures. Render is mandatory;
evaluations, captures, interception, frames, persistent/headful sessions,
proxy policy, and navigation transport overrides each require their matching
declared capability. Capture/evaluation results must also obey their individual
plan byte limits, and artifact-only captures cannot return inline chunks.
Every navigation/action/evaluation owner ID is injective and exactly exhausts
the plan's operation list. Multi-page pagination reserves its first page on
the action and carries exactly `max_pages-1` additional, predeclared page
operation IDs. Dynamic page allocation is rejected in v1.

## Fencing and commit ownership

`FencingContext` canonically hashes, in field order, `shard_id`, decimal
`routing_epoch`, decimal `engine_owner`, `claim_token`, `lease_id`, and
`config_revision` as `SHA256("crawler.runtime/v1/fence" || NUL ||
join(NUL, fields))`. The 32-byte digest and current attempt ID are mandatory on
window updates, resume rejection, and every execution frame; full context on
start, resume, and cancel must match. Validators may also compare the request
to a live caller context.
All textual components reject NUL, so the joined digest input is injective.

The executor never declares persistence authority. Immediately before every
Redis, Postgres, or catalog mutation, the owning caller must re-read and
compare the current #7938 live claim token and routing epoch (plus lease/config
identity). A valid `eligible_for_commit` terminal never replaces that check;
stale mismatch rejects and counts the attempted mutation.

## Bounded transfers and extensions

Hard ceilings are 1 MiB per framed record, 8 MiB inline memory, 8 MiB per
artifact chunk, 16 MiB per HTTP transfer, and 64 MiB aggregate browser output.
An execution has at most 4,096 unique frames and 64 artifact handles totaling
at most 64 MiB.
`ChunkManifest` has at most 64 ordered chunks with contiguous sequence,
per-chunk size/digest/storage, total size/digest, and mandatory completeness.
Validation hashes inline data incrementally. Inline data in `ExecutionFrame`
also obeys the lower 1 MiB frame ceiling; larger results use artifact handles.

Common configuration/output is typed. Provider-specific semantics use only a
registered 64 KiB `ExtensionEnvelope` with schema ID, version, encoding,
canonical payload, and SHA-256. The closed v1 registry also validates exact
JSON shape and placement: monitor config on manifests, scraper config on
manifests, runtime metadata on job content/monitor metadata, and browser
evaluation values only on evaluation output. Unknown schemas, extra keys,
invalid values, and wrong contexts reject. Bounded diagnostic
details are observational only and never drive policy, routing, retry, diff,
gone, watermark, or persistence behavior.

## Cancellation, deadlines, artifacts, and authority

Deadlines use strict RFC3339 with an explicit offset. `ServerHello.accepted_at`
is the sole clock basis: the deadline must be later and no more than 15 minutes
after acceptance. W3C `traceparent` rejects zero IDs, version `ff`, and v00
extensions; `tracestate` requires a parent, unique valid keys, and bounded
members. `CancelRequest` propagates
request, attempt, full fencing context, and caller cancellation. A late
response remains stale.
A typed `retry_after_ms` scheduling hint may be at most 7 days and grants no
scheduling authority.
Artifacts are opaque handles plus media type, size, hash, and redaction flag;
filesystem paths never cross the boundary. Bodies, artifacts, frames, output,
batch counts, action/capture/evaluation counts, active time, and frame credit
all have negotiated finite limits.

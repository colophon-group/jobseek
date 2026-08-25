# Candidate framed execution protocol v1

This proposal is not wire-authoritative until #7937 selects the transport,
replaces opaque plan fields, generates both language bindings, and verifies
limits, artifacts, cancellation, framing, and backpressure in conformance
tests.

This is the language boundary between the authoritative worker and a replaceable
extractor. The first Go cutover keeps Redis claiming, leases, circuits, gone
policy, Postgres writes, and R2 staging in the sole Python worker and delegates
only extraction. Later the Go worker can consume the same contracts directly.

Transport may be Unix-socket Connect/gRPC or length/framed JSONL. Transport
choice must preserve these semantics:

- One `execution-request` starts a monitor or scrape with a stable semantic
  `origin_request_id`, transport request/attempt IDs, deadline, trace context,
  immutable config revision/fingerprint, and normalized config.
- Monitor responses stream ordered `monitor_batch` frames with contiguous
  sequence numbers followed by one terminal frame.
- Scrape responses contain exactly one `scrape_result` or `error`, then one
  terminal frame.
- Cancellation is propagated. A response received after deadline, cancellation,
  claim-token loss, or routing-epoch change is stale and cannot mutate state.
- Backpressure is bounded. The producer may not buffer an unbounded board in
  memory; maximum frame/body/field sizes are negotiated and enforced.
- Artifacts cross the boundary as opaque handles and metadata, never host-local
  filesystem paths.
- A disconnect after dispatch is ambiguous: the extractor may already have
  contacted the origin. It must resume or return a deduplicated result by
  `origin_request_id`; otherwise the owner fails closed into the normal policy
  reschedule path. It must never blindly re-execute the origin request with a
  fresh ID. `attempt_id` identifies transport attempts, not new semantic work.
- Error classes are stable protocol values. Human exception strings are
  diagnostic only and never choose gone/tombstone policy.

The conformance suite injects disconnects immediately after dispatch, before
and after each response frame, and after a result but before the terminal
frame. Every case must prove at-most-once origin contact per semantic request,
bounded cleanup, and a non-authoritative result until a valid terminal frame.

The execution service has no authority to reschedule, mark gone, delist,
increment failure budgets, or write final posting state.

# Authoritative crawler runtime contract v1

This directory is the authoritative language-neutral boundary between the
crawler control plane, scheduler, extraction runtimes, and persistence
pipeline as of crawler release `0.13.590`. `runtime.proto` is the wire
authority. The checked-in Python and Go bindings are deterministic products of
that file; `activation.md` records their exact compiler, runtime, corpus, and
predecessor evidence.

The contract is intentionally about semantics, not an RPC choice. The first Go
segments may run in the same worker process/container topology or behind a
short-lived migration adapter. The final worker may consume Redis and Postgres
directly. Either way, it must produce the same normalized payloads and metrics.
This activation packages the boundary but does not enable a production Go
worker or change crawler routing.

Files:

- `runtime.proto` — authoritative v1 IDL.
- `gen/go/` and `python/jobseek_runtime_v1/` — generated bindings; regenerate
  with `./generate.sh` and verify byte stability with `./generate.sh --check`.
- `framing/` and `privacy_registry.json` — wheel-packaged framing and privacy
  assets used by both installed-artifact smoke tests.
- `fixtures/` and `conformance/` — shared framing, compatibility, control,
  redaction, semantics, and source-identity corpora for Python and Go.
- `baseline/` and the adjacent-version policy specimen — immutable descriptor
  evidence and the required breaking-change converter policy.
- The retained `*.schema.json` files document the pre-activation normalized
  JSON boundary. They are not a second wire authority and are not retired by
  this activation.
- `queue.md` — Redis/Lua scheduling, lease, and politeness invariants.
- `metrics.md` — cross-runtime metrics required for cutover and reversal.

Compatibility rules:

1. Producers add optional fields; they do not change existing field meaning.
2. A breaking change creates a new version directory and a replay converter.
3. Unknown configuration fields are preserved by the control plane but may be
   ignored by a runtime only after fleet/replay evidence proves they are unused.
4. Output comparison is semantic: URL ordering is ignored, HTML is normalized
   before comparison, and absence is distinct from an explicit empty value.
5. No runtime may write final crawl state without the queue lease and database
   guards documented in `queue.md`.

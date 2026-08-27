# Candidate crawler runtime contract v1

This directory captures the candidate language-neutral boundary between the
crawler control plane, scheduler, extraction runtimes, and persistence
pipeline. It is provisional until #7937 chooses the transport, closes the
opaque schema fields, generates Python/Go types, and promotes the IDL. No Go
consumer may treat these files as wire-authoritative before that gate. CSV,
Redis, Postgres, `ws`, and Murmur representations will be normalized before
they reach the promoted runtime contract.

The contract is intentionally about semantics, not an RPC choice. The first Go
segments may run in the same worker process/container topology or behind a
short-lived migration adapter. The final worker may consume Redis and Postgres
directly. Either way, it must produce the same normalized payloads and metrics.

Files:

- `board-runtime-config.schema.json` — normalized board configuration.
- `monitor-result.schema.json` — one streaming discovery batch.
- `scrape-result.schema.json` — one extracted posting payload.
- `execution-request.schema.json` / `execution-frame.schema.json` — framed,
  cancellable Python/Go extraction protocol.
- `browser-plan.schema.json` / `browser-result.schema.json` — typed browser
  capability boundary that does not expose raw CDP or Playwright objects.
- `fixtures/source_identity/` — frozen Python/Go wire and JSON compatibility
  vectors for the optional durable source-identity amendment.
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

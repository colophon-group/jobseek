# Golden offline replay v1

The corpus stores ordered, bounded `CapturedExchange` messages. An upstream
response is captured once by the exclusive queue owner, redacted, and
then replayed without network access. Live Python/Go shadow requests are
forbidden.

Each contacted semantic origin operation has one stable ID and exactly one
matching exchange. Exchanges begin with the operations declared on
`ExecutionRequest`; later contiguous entries are dynamic operations whose full
refs also appear in pre-dispatch `OriginOperationDeclared` frames. Ordered
request/response chunk sizes, per-chunk digests, total sizes/digests,
completeness, and request operation refs are verified before decoding. Request
methods are bounded uppercase HTTP tokens, response statuses are 100–599,
headers are canonical and bounded, and semantic hashes have an exact SHA-256
shape. Each `OriginContact.request_fingerprint` is exactly lowercase hex
`SHA256(deterministic protobuf CapturedRequest)`, binding method, canonical URL,
headers, and the body manifest to the operation contact. The checked-in v1
corpus deliberately requires all-inline normalized
payloads of at most 8 MiB; no artifact resolver API is claimed. Larger
16/64 MiB transfer shapes are covered by structural chunk conformance, not by
the representative decoder corpus. Inline chunks are hashed incrementally before
the bounded normalized payload is assembled. Replay adapters in
this foundation decode representative normalized monitor and scrape JSON;
provider-specific decoders and exhaustive browser capability transcripts are
added by #7953–#7963.

Exchange count is independent of normalized result count. An exchange may
have no normalized result, while every normalized monitor/scrape result frame
must be referenced exactly once by
`CapturedExchange.normalized_result_frame_sequence`. The representative
dynamic replay proves two initial operations plus a later dynamic operation
can produce one normalized scrape result.

Parity compares:

- all normalized frames and optional-field presence;
- exact URL/job membership after ordering canonicalization;
- content hashes, `hybrid`, `truncated`, both filtered counts, sitemap
  replacement, and metadata-update hash;
- projected database effects, including whether gone detection is allowed;
- the deterministic semantic hash over length-prefixed deterministic protobuf
  frames and projection.

The projection binds `request_id`, `origin_request_id`, execution kind, and the
request target URL. It also contains a URL-keyed, sorted target map whose value
is the action plus optional content hash: monitor URLs/jobs and scrape source
URLs are `UPSERT`, and a successful browser target is `BROWSER_RESULT`.
Therefore a request target mutation changes the semantic hash even if result
frames are unchanged. Gone detection is false for truncated or
security-filtered monitor output.

The metadata-update projection is itself lossless and ordered: SHA-256 receives
the unsigned 64-bit big-endian length and deterministic protobuf bytes of each
`MonitorMetadataUpdates` message in frame order. This preserves repeated
extensions and per-batch presence instead of applying a lossy map overwrite.

`tools/redaction.py` defines deterministic pseudonyms as
`SHA256(UTF8(scope) || NUL || UTF8(value))`. Scope strings are stable and
location-specific: `header:<lowercase-name>`, `query:<lowercase-name>`, and
`body:<request|response>:<RFC6901 JSON pointer>`. Sensitive headers must be
marked redacted and contain only `redacted-sha256:<64 lowercase hex>`.
Personal email pseudonyms use the same scope and the reserved
`person-<64 lowercase hex>@redacted.invalid` shape. Validators decode and scan
every inline request/response chunk, joined bodies (so chunk boundaries cannot
hide a token), URLs/query parameters, and headers. Raw cookies, authorization
values, API keys, JWTs, private keys, URL credentials, secret assignments, and
real email addresses reject. `deterministically_redacted` is an assertion for
audit output, never trusted as evidence or used to skip scanning.

The hard v1 ceilings are 1 MiB framed records, 8 MiB inline bodies/artifact
chunks, 16 MiB HTTP transfers, and 64 MiB browser transfers. The representative
corpus is intentionally small. Its purpose is contract and failure safety, not
exhaustive provider coverage.

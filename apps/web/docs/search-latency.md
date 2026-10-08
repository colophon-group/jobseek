# Public search latency measurements

`GET /api/v1/search` emits a `public_api.search_latency` JSON log after
constructing its response, alongside `public_api.request` in the runtime
record. Only finite stage names, verified consumer, status class and numeric
measurements are recorded. No queries, filters, results, URLs, credentials,
IPs or customer identifiers are collected. CDN hits and WAF rejections do
not execute this instrumentation.

Stages: `rate_limit`, `parse_filters`, `resolve_filters`, `interpret_terms`,
`session`, `provider`, `main_search`, `year_counts`, `company_metadata`,
`active_counts`, `posting_hydration`, `location_lookup`, `occupation_lookup`,
`seniority_lookup`, `technology_lookup`. Only executed stages appear. Queries,
cache keys, retry policy, ordering and public response contracts are preserved.

- `duration_ms`: monotonic handler wall time, excluding module startup,
  response delivery and post-response metrics writes.
- `calls`, `wall_ms`, `wall_max_ms`: invocation count, cumulative stage wall
  time and slowest invocation.
  Nested and parallel stages overlap: **do not add them to estimate total**.
- `sdk_calls`, `sdk_errors`, `sdk_wall_ms`: observed terminal Typesense SDK
  calls, including SDK-internal retries within a call. These are not HTTP
  attempt counts. SDK wall includes transport, queueing and response parsing.
- `engine_samples`, `engine_ms`, `engine_max_ms`: finite `search_time_ms`
  values returned by Typesense, their sum and maximum. Samples can be absent.
- `application_retries`: additional attempts scheduled by the application.
  Silent SDK-internal retry counts remain unavailable.

Next.js executes `use cache` fills in a clean context. Their SDK calls can be
outside the request timing scope, including shared/background fills. Outer
parsing stages still measure caller wait. **Zero observed SDK calls does not
establish a cache hit.** Finished profiles ignore late background completion.
Timing profiles are not placed in cached values or response headers.

Compare successful outcomes in equal coverage windows. First compare parsing
with provider time, then main query, yearly counts and company hydration (or
active counts and posting hydration for top-company requests). SDK wall versus
reported engine time separates engine execution from the remaining combined
transport/retry/client overhead; it cannot separate network from queueing.
Do not sum marginal percentiles or infer zero traffic from absent fields.

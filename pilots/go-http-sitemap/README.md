# Go HTTP + sitemap pilot

This is an isolated, non-production Phase 0 vertical slice for issues #7949 and #7953,
frozen against Python behavior on repository base
`38f116a3fb9ac0893676673498c94e945ed57843`.

The admitted cohort is intentionally small: an explicit configured sitemap
URL, GET only, XML `urlset` or a one-level `sitemapindex`, namespace-neutral
parsing, job-child preference, 404/410 child skipping, duplicate removal,
`utm_*` removal, literal include/exclude filters, and literal prefix rewrite.
The test corpus includes a frozen `abbvie-careers` repository configuration
whose `/job/` Python regex is provably equivalent to literal containment for
the fixture input; this is not general Python-regex compatibility.
Redirects are rejected as status errors rather than followed.
All results are held until the complete index succeeds, so a child error never
publishes a partial URL set. HTTP reads have per-response decoded-byte,
aggregate decoded-byte, and request-count limits; cancellation interrupts an
in-progress read.

The explicit root sitemap GET has a narrow retry policy for observed transient
failures: empty 200 responses; status 202, 401, 403, 408, 425, 429, or any 5xx;
and typed transport/timeouts. It makes three attempts total by default and at
most, with context-aware deterministic waits of 500 ms and then 1 s. Children are
single-attempt. Cancellation and config, body, aggregate, request-cap, 404/410,
other 4xx, and nonempty malformed XML failures are never retried.

For wire-accurate Phase 0 accounting, each counted GET uses a fresh HTTP/1
connection. This prevents `net/http` from transparently retrying a GET on a
stale reused connection, but deliberately sacrifices connection pooling and
HTTP/2 throughput. A pooled, protocol-flexible transport with wire-attempt
instrumentation remains a production blocker. In particular, the hermetic
connection-aware fixture documents that this fresh-connection policy cannot
recover an origin that returns 500 for the first request on every connection
and 200 only for a later request on that same connection.

This directory has no production wiring and does not import crawler contracts,
Redis, Postgres, browser code, or publisher code.

Run the candidate checks from this directory:

```sh
gofmt -w boundedhttp/*.go sitemap/*.go
go test ./...
go test -race ./...
go vet ./...
```

Not implemented and therefore production-blocking: Python parity corpus,
child-request retries, auto-discovery/rediscovery,
nested indexes/cycle handling, TDM reservation, proxy and skip-TLS inputs,
Python-regex-compatible filters/transforms, transcript capture, benchmark/CPU/
RSS evidence, and any queue, persistence, or deployment ownership. Unknown root
documents, nested indexes, and invalid configuration fail closed. Individual
entries without a usable `loc` are ignored and can yield an empty success,
matching the inherited Python extraction behavior. No migration ROI or
production-readiness conclusion can be drawn from this candidate.

Sessions are single-goroutine. For parity with the Python monitor, the URL cap
is applied before duplicate removal and configured filtering; that inherited
ordering can omit otherwise qualifying URLs and must be revisited before
production admission. Dedicated path-aware Linux CI is required for every
change to this module; local checks alone cannot authorize merge.

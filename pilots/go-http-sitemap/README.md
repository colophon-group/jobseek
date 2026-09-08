# Go HTTP + sitemap pilot

This is an isolated, non-production Phase 0 vertical slice for issues #7949 and #7953,
frozen against Python behavior on repository base
`38f116a3fb9ac0893676673498c94e945ed57843`.

The admitted cohort is intentionally small: an explicit configured sitemap
URL, GET only, XML `urlset` or a one-level `sitemapindex`, namespace-neutral
parsing, job-child preference, 404/410 child skipping, duplicate removal,
`utm_*` removal, literal include/exclude filters, and literal prefix rewrite.
UTM-removal parity is admitted only for query strings accepted by Go's strict
query parser whose decoded keys and values are valid UTF-8. Unsupported syntax
(including malformed percent escapes and raw semicolons) is preserved
byte-for-byte. Percent-decoded invalid UTF-8 follows Go-specific encoding
behavior. Both forms remain outside the parity cohort.
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

By default, each sitemap invocation owns an HTTP/1 connection pool, so explicit
root retries and child fetches can reuse a connection without leaking idle
state to another invocation. The concurrent-worker pilot can opt into one
explicitly bounded process-owned transport while retaining task-local sessions,
budgets, and counters. Every admitted GET carries a zero-byte, non-replayable
body. It is bodyless on the wire, but prevents Go's HTTP/1 transport from
transparently replaying an idempotent GET on a stale pooled connection. The
request cap therefore bounds explicit transport attempts; `WireAttempts`
separately records `net/http`'s request-write hook and must never exceed the
admitted `Requests`. A hermetic connection-aware fixture proves recovery when
an origin returns 500 for the first request on a connection and 200 for the
second. HTTP/2 remains excluded because it has a separate transparent retry
path.

The `worker` package adds a non-production, fixed-goroutine scheduler with
bounded admission/results, origin-fair dispatch, task deadlines, panic
containment, and bounded shutdown cancellation. Its fixed-resource comparison
method is frozen in [WORKER-BENCHMARK.md](WORKER-BENCHMARK.md).

This directory has no production wiring and does not import crawler contracts,
Redis, Postgres, browser code, or publisher code.

Run the candidate checks from this directory:

```sh
gofmt -w boundedhttp/*.go sitemap/*.go
go test ./...
go test -race ./...
go vet ./...
```

Not implemented and therefore production-blocking: Python fleet parity corpus,
child-request retries, auto-discovery/rediscovery,
nested indexes/cycle handling, TDM reservation, proxy and skip-TLS inputs,
Python-regex-compatible filters/transforms, transcript capture, benchmark/CPU/
RSS evidence, and any queue, persistence, or deployment ownership. Unknown root
documents, nested indexes, and invalid configuration fail closed. Individual
entries without a usable `loc` are ignored and can yield an empty success,
matching the inherited Python extraction behavior. No migration ROI or
production-readiness conclusion can be drawn from this candidate.

Sessions remain single-goroutine and are never shared between tasks; only the
underlying transport may be shared. For parity with the Python monitor, the URL
cap is applied before duplicate removal and configured filtering; that
inherited ordering can omit otherwise qualifying URLs and must be revisited
before production admission. Dedicated path-aware Linux CI is required for
every change to this module; local checks alone cannot authorize merge.

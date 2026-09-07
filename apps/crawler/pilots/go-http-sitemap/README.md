# Go HTTP + sitemap pilot

This is an isolated, non-production Phase 0 vertical slice for issues #7949 and #7953,
frozen against Python behavior on repository base
`38f116a3fb9ac0893676673498c94e945ed57843`.

The admitted cohort is intentionally small: an explicit configured sitemap
URL, GET only, XML `urlset` or a one-level `sitemapindex`, namespace-neutral
parsing, job-child preference, 404/410 child skipping, duplicate removal,
`utm_*` removal, literal include/exclude filters, and literal prefix rewrite.
Redirects are rejected as status errors rather than followed.
All results are held until the complete index succeeds, so a child error never
publishes a partial URL set. HTTP reads have per-response decoded-byte,
aggregate decoded-byte, and request-count limits; cancellation interrupts an
in-progress read.

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
retries/backoff and typed retry exhaustion, auto-discovery/rediscovery,
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
production admission. This module is not currently exercised by repository CI,
so local checks cannot authorize merge.

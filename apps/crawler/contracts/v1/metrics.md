# Runtime cutover metrics v1

Python and Go export the same metric names and label values. `implementation`
is `python` or `go`; browser `backend` is `chromium` or `lightpanda`.

Required replacement-boundary series:

- `crawler_runtime_executions_total{stage,implementation,outcome}`
- `crawler_runtime_execution_duration_seconds{stage,implementation}`
- `crawler_runtime_output_items_total{stage,implementation}`
- `crawler_browser_backend_lifecycle_total{backend,event,outcome}`

Extraction outcomes are the bounded set `success`, `error`, `cancelled`, and
`incomplete` (the authoritative caller stopped consuming a stream before its
terminal frame, for example after a persistence failure).

Existing end-to-end series remain the source of truth for service outcomes:

- task success/failure/gone/deferred totals and duration histograms;
- queue due age/depth, inflight leases, lease reaping, and dead letters;
- monitor discovered/new/relisted/gone/truncated/filter counts;
- scrape success, empty-result/transient/permanent-gone classes;
- host/provider circuit state, 403/429/5xx/timeout rates, and retry recovery;
- Postgres/R2/export/Typesense lag and reconciliation drift;
- process/container RSS, CPU, OOM/restart count, and work per GiB-hour.

Cutover comparison uses rates and distributions, not absolute current volume.
Every segment records the implementation/build version in logs and deployment
metadata so a regression window maps to one binary and configuration snapshot.

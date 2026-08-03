# Alloy telemetry rejection and OOM incident — 2026-08-03

Issue: [#6126](https://github.com/colophon-group/jobseek/issues/6126)

## Impact

Grafana Cloud rejected host remote writes when the tenant crossed its 15,000
active-series and 1,500-items/s limits. Independently, crawler Compose Alloy
was OOM-killed about every two hours under its 256 MiB cgroup limit. Metrics
and logs were therefore least trustworthy during the service incidents they
were expected to explain.

## Evidence and cause

Production journals recorded 85 crawler-host, 338 PostgreSQL-host, and 37
Typesense-host HTTP 429 responses in the reviewed 48-hour window. The fresh
crawler Compose process reached roughly 262 MiB cgroup usage against its
256 MiB limit while `docker stats` showed much less because it subtracts
inactive file-backed pages. Kernel OOM records showed the same repeating
failure at approximately 259 MiB anonymous plus 85 MiB file RSS.

Two avoidable series multipliers dominated the tenant. Full host Alloy
self-scrapes emitted about 1,565 series per host, mostly internal histogram
buckets. Crawler circuit-breaker gauges and counters retained one
`egress_host` series per real career-site origin in every long-lived worker,
so their cardinality grew toward the number of configured companies rather
than a fixed operational bound. Redis per-command histograms added hundreds
more series without serving a production alert.

## Resolution

- Compose Alloy receives a 512 MiB hard limit, 256 MiB Go soft target, and 0.5
  CPU. Native collectors receive a 512 MiB systemd limit and 384 MiB Go target.
- Every Prometheus remote-write endpoint uses one shard and a bounded
  4,000-sample queue, preventing replay concurrency from overwhelming the
  1,500-items/s tenant limit.
- Per-origin circuit metrics are dropped before remote write. Bounded task
  outcomes retain detection; Loki events retain origin-level diagnosis.
- Redis metrics use an operational allowlist and omit command histograms.
- The independent root sampler republishes a fixed Alloy health surface,
  including readiness, RSS, send freshness, backlog, HTTP 429s, failed/dropped
  samples, and dropped Loki entries. It also inspects Compose Alloy restart and
  OOM state.
- Deployment enforces a 12,000 total-series ceiling plus per-job budgets before
  alert rules are synced. Dedicated alerts cover rejection, staleness, backlog,
  loss, memory pressure, restart/OOM, and renewed series growth.

## Acceptance

The change is accepted only after configuration/unit tests, Alloy validation,
transactional production rollout, fresh four-collector sampler coverage, all
series budgets passing, zero new HTTP 429 responses, and a soak with no Alloy
restart or OOM. The repo cannot truthfully compress the issue's seven-day
stability requirement into a single deploy; the new independent alerts and
fixed acceptance queries preserve that evidence window after rollout.

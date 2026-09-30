# Native ordinary worker authority checkpoint

The full [Go and Lightpanda migration plan](27-go-lightpanda-continuation-plan.md)
remains active. This checkpoint continues draft
[PR #10207](https://github.com/colophon-group/jobseek/pull/10207). It is an
unselected authority foundation, with no ordinary native executable or
production ownership change. Its
[module contract and next gates](../apps/crawler/go/ordinary-queue/README.md)
are the implementation handoff.

The latest merged production checkpoint is
[PR #10209](https://github.com/colophon-group/jobseek/pull/10209), merge
`8d5e17e94f949c8a5e35c53878c988b7d3ad4c9a`. Crawler v0.13.902 remains
`b75ccb9456bf29c9477f9747c0c2cc3908ad79bb`, with the native B0 executor at
restored epoch 145. See the
[native B0 production checkpoint](29-native-go-b0-executor-checkpoint-2026-09-30.md)
for exact images, supported cold reversal and the first naturally due Bunq
commit. This entry makes no fresh host health attestation. Ordinary workers
remain Python; production Python/Playwright/Chromium retirement is incomplete.

## Prepared authority slice

The previous Redis attempt generation now binds monitor/detail activation,
canonical write transactions and settlement to a retained PostgreSQL fence.
The library reuses the current B0 global routing epoch and adds a shared lease
barrier so reaping cannot end native authority during a database transaction.
The production Go reaper participates; legacy direct reaping skips tokenized
attempts. Terminal receipts retain canonical deadlines, including unscheduled
details. A replacement attempt recovers unchanged committed output after a lost
acknowledgement, including across ownership retirement, without repeating effects.

Local race tests pass with real private Redis and fully migrated PostgreSQL
18.6. They verify delayed expired activation, callback/cancellation/configuration
rollback, commit ordered before epoch retirement, stale schema/write/queue
operations, exact deadlines, NULL detail completion, modeled commit-before-ack
recovery and the production Go reaper barrier. The actual migration passes
upgrade/downgrade/re-upgrade. Focused Python queue/reaper/pool checks pass
(130 tests); Go vet/module checks and workflow lint/security checks pass.
The workflow now requires real PostgreSQL 17 and Redis on Linux amd64 and arm64.
Exact-head CI results belong in the PR; earlier token-only runs do not prove the
new database authority. Candidate VERSION remains v0.13.903.

These fixtures prove authority ordering and atomic canonical test effects.
They do not prove native ordinary extraction/enrichment parity, an actual
ordinary process crash, installed ownership, full cold reversal or fleet cost.
Raw queue primitives are not database write authority. The `guarded` Lua argument
is a trusted wrapper convention, not a security capability.

## Next delivery and completion gates

At 20:15:09 UTC, a read-only production census recorded 8,019 boards: 7,885
enabled and 134 disabled. Of enabled boards, 522 had stored browser requirements
(419 monitor, 264 scraper, overlapping); Greenhouse accounted for 2,280 boards,
741 naturally due, four with existing failures and none never successful.
Stored family/browser flags do not establish effective profile or native coverage.

Prioritize a bounded standard Greenhouse HTTP/API cohort after effective
configuration validation. First bind exclusive profile/domain ownership to
existing claims and the verified active epoch. Preserve unselected ready work,
domain fairness/rate limits, deferred monitors, repair and never-successful jobs.
Then connect native monitor/detail fetch, enrichment, canonical persistence,
complete/truncated inventories, disappearance, retry/circuit/publisher policy,
description deduplication/R2 and database-owned scheduling. Reuse the existing
Go Greenhouse parser and shared native enrichment/persistence.

Before selecting that cohort, prove installed process cancellation and actual
commit-before-ack crash recovery; supported all-writer quiesced cutover and cold
reversal; exact image/output/freshness/queue evidence; and fresh required CI plus
the actual Crawler Deploy Gate. Coordinate B0 and ordinary ownership because
retiring their shared global epoch invalidates both. Never adopt the allocator's
latest value without a verified active ownership plan.

Expand to every enabled effective profile, including suspect/gone/quarantined
rows, then replace remaining Python scheduling/maintenance/deployment consumers.
Measure comparable whole-service CPU/RAM/density/attributable cost and complete
the actual rollback window before removing production Python, Playwright,
Chromium and legacy runtime-only assets. Preserve useful isolated offline Python.
A first cohort or this authority library does not complete the migration goal.

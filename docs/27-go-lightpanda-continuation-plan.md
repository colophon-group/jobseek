# Go and Lightpanda migration delivery plan

Updated 2026-10-04. The full migration goal remains active.

## Delivery objective

Replace every enabled crawler monitor/detail/browser profile with production Go
and self-hosted Lightpanda or proven Go HTTP/API routes. Replace mandatory Python
service, scheduling, deployment and maintenance consumers. Remove production
Python, Playwright, Chromium and runtime-only legacy assets after the supported
rollback window; preserve useful isolated offline Python and every enabled board.

Completion requires correct canonical fields and database effects, publisher
policy, description/R2 behavior, freshness and queue conservation; comparable
whole-service CPU/RAM/density/attributable cost; and a supported cold reversal
with its observation window. A merged candidate or synthetic benchmark does
not complete migration.

## Latest checkpoint

v0.13.921 is deployed at `5e85ce5e526d29ed96c4325ab9b5a20e26c34cda` by
[deployment 37186188736](https://github.com/colophon-group/jobseek/actions/runs/37186188736).
[PR #10264](https://github.com/colophon-group/jobseek/pull/10264) fixes canonical
deadline recovery for completed native receipts after a lost settlement ACK.
Required CI, the actual Crawler Deploy Gate and installed image parity passed.

The 4,297-board activation reached SQL active ownership, but the B0 producer
lost Redis preflight authority during startup. The host receipt had already
been published active before restart arming failed. Original supported retirement
succeeded: the ordinary SQL plan is retired, its Redis projection/host receipt
and native container are absent, and all nine legacy/B0 writers run at the exact
selected immutable images with restart policies armed. All seven health endpoints
passed independent readback. **Ordinary native boards currently serving: zero.**
B0 remains active at epoch 163 with 22 members; four Python workers serve the
ordinary fleet. Source921 production lost-ACK expiry proof remains pending.

Redis slowlog contains 63 ordinary claims among its latest 64 slow commands,
with a peak of 198 ms. Every claim decodes the full 4,297-member configuration
payload. This plausibly contributed to producer preflight failure; the exact
Redis error family is unproven. The next runtime fix uses a smaller routing
projection bound to the full immutable SQL plan and publishes active host state
only after full restart arming. A private Redis comparison using the actual
retired production payload reduced projection bytes from 3,437,948 to 216,221,
mean claim time from 33.0 to 6.1 ms and a 64-call burst from 2.12 to 0.40 seconds.
This is a local microbenchmark, not production readiness or whole-service cost
acceptance. Retained older plans preserve their original projection for recovery.

Earlier source920 served 4,297 boards across eight profiles: Greenhouse 2,570,
Ashby 934, Lever 194, Recruitee 114, Pinpoint 104, Teamtailor RSS 137,
SuccessFactors RSS 197 and Personio 47. All eight recorded native successes
before retirement, but the normal reaper produced stale completed deadlines;
original retirement restored canonical scores. All 7,885 enabled boards remain
preserved; 3,588 other boards span 100 crawler types.

Workday inventory discovery is committed separately, including multi-site,
requisition deduplication, search, deep pagination, facet partition/union and
independent coverage checks, plus the sealed native HTTP boundary. Real Python
parity cases and focused race/vet checks pass. Native URL-only persistence,
detail scheduling and owned detail execution remain to be integrated before
that code adds production coverage.

See [source921 restoration evidence](evidence/go-native-family921-restoration-2026-10-04.json).

## Continue delivery

1. Deliver the measured Redis claim fix and correct host receipt publication,
   then prove stable 4,297-board serving and committed native receipt recovery with
   canonical deadline conservation beyond lease expiry. Observe real scheduled
   completions across every admitted provider. Verify SQL/Redis deadlines, canonical fields,
   descriptions/uploads, publisher outcomes and whole-service freshness.
2. Deliver native Workday monitor/detail and generic HTTP/Lightpanda execution,
   reusing the existing queue, writer, enrichment and parser contracts. Cover
   supported configuration variants, then the remaining provider profiles.
   Measure enabled serving owners and successful native completions. The registry
   has 496 Workday boards: 394 use multi-site discovery and 102 explicit single-site
   discovery. Include configured search/facet/site variants and their detail
   assignments in this delivery; refresh production admission before ownership.
3. Replace the remaining Python worker/browser entrypoints and mandatory
   startup/deployment/maintenance commands, including migration/credential
   preparation and activation/reaper consumers. Retain useful Python parity
   tools outside the production runtime.
4. Observe comparable production whole-service resources and attributable cost,
   prove conservation and freshness under normal schedules, and exercise the
   supported full cold reversal. Keep the rollback image for its observation
   window.
5. Remove Python, Playwright, Chromium and runtime-only legacy assets when all
   enabled profiles and mandatory consumers have native authority. Verify the
   complete production image and operational paths after removal.

Batch compatible implementations into useful releases. Finish each rollout with
serving ownership and real completions before treating it as migration progress.
Add infrastructure or fixtures only for a changed contract or observed failure.
At each checkpoint record remaining enabled profiles and production Python
consumers. Keep implementation, staging, serving and observation status distinct.

## Operational handoff

Use the installed source-bound drivers and immutable images. Respect deployment
holds, scheduled reconciliation, the mutation lock and exclusive SQL barriers.
Wait for live legacy leases to expire naturally. If expired tokenless monitors
block activation, preserve the exact pending receipt and cold lane, use the
existing source-bound maintenance reaper, then retry **activate with the same
plan/projection hashes**. `recover-pending` cancels an incomplete activation and
restores the full legacy stack; it does not resume activation. Do not clear
leases/fences, kill foreign one-offs or partially restart writers to force progress.

Earlier production observations remain in
[source17 evidence](evidence/go-native-family917-production-2026-10-04.json),
[v0.13.913 evidence](evidence/go-native-family913-production-2026-10-03.json)
and Git history. Follow [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md)
and [Hetzner maintenance](16-hetzner-maintenance.md) for deployment and recovery.

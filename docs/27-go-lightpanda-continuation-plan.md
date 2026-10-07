# Go and Lightpanda migration delivery plan

Updated 2026-10-07. The full migration goal remains active.

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

## Latest checkpoint — 2026-10-07

Release **0.13.958**, source `82c73de3ea432634c5e365c5f57dfc02873a4bf6`,
completed original [deployment and promotion37564684440](https://github.com/colophon-group/jobseek/actions/runs/37564684440).
Independent readback verified exact slim/browser digests, seven restart-armed
base processes, six healthy endpoints and cold epoch208. Supported B0 activation
and independent source/receipt/health readback then passed at epoch209. Ordinary
Go staging is running; this checkpoint does not claim ordinary serving yet.

Fresh source-bound admission covers7,159 monitor boards and2,617 detail boards,
with1,468,186 of1,493,765 scheduled detail postings. Forty detail boards retain
legacy authority because route/cache reconciliation failed. Preserve their
current owner and every enabled board while completing remaining coverage.

[PR #10347](https://github.com/colophon-group/jobseek/pull/10347) combines DOM
rich rows over direct/proxy/Lightpanda paths, HR Manager RSS, and shared rich-job
filters/provider allowlists across Generic RSS, SuccessFactors, Teamtailor,
rich DOM and URL-only DOM. Release0.13.960 has90 compiled profiles. Full worker
and queue race suites pass502.851/301.919 seconds against the identical crawler
tree. Fresh required/native/installed CI remains necessary. Keep these changes
in one delivery to share CI, deployment, census and cutover.

Self-hosted Lightpanda is pinned to the latest verified stable **1.0.0**.
The next grouped RSS batch has legacy XML and structured-summary parser cores
with31 actual Python reference cases; configured transports, pagination,
WordPress feeds, detail properties, canonical writes and cold proof remain.

See the [current worker checkpoint](30-native-ordinary-worker-checkpoint-2026-10-03.md)
and [combined filtering evidence](evidence/go-native-shared-feed-policy-candidate-2026-10-07.json).

## Continue delivery

1. Finish source958 ordinary staging/activation and observe real scheduled
   monitor/detail completions across admitted profiles. Verify canonical fields,
   SQL/Redis deadlines, descriptions/uploads, publisher outcomes and health.
   Use supported pending recovery/reversal if activation fails.
2. Deliver the combined0.13.960 PR after exact-head CI and merge authority.
   Continue grouped remaining RSS variants and DOM pagination/empty-state/portal
   options, then remaining monitor/detail/browser types. Use fresh canonical
   admission to choose useful batches and measure actual native coverage.
3. Replace mandatory Python consumers in compatible groups: worker/browser
   `crawler run`/`run-browser`; deployment `crawler sync` and schema preparation;
   activation/epoch/reaper and maintenance commands. Reuse the existing Go queue,
   persistence and operator contracts. Keep useful Python reference/labelling
   tools isolated outside production crawler execution.
4. Observe comparable whole-service CPU/RAM/density/attributable cost and
   normal-schedule freshness/conservation. Exercise supported full cold reversal
   and retain rollback evidence/images for the observation window.
5. Remove production Python, Playwright, Chromium and legacy runtime-only assets
   once all enabled profiles and mandatory consumers have replacement authority.
   Verify the complete native image, startup, deployment and maintenance paths.

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

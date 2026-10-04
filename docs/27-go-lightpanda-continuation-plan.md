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

v0.13.920 is deployed at `95bbabd78681be9046367a8ad6b3beba77a0e001` by
[deployment 37179937902 attempt 2](https://github.com/colophon-group/jobseek/actions/runs/37179937902).
The selected release, success marker and live environment agree on the approved
immutable images. [PR #10262](https://github.com/colophon-group/jobseek/pull/10262)
adds native Teamtailor/SuccessFactors rich RSS and fixes observed provider
selection starvation. [PR #10263](https://github.com/colophon-group/jobseek/pull/10263)
adds native Personio XML/domain/HTML fallback and ordered localized titles/locales.
Both passed Required CI, the actual Crawler Deploy Gate and installed image parity.

Original B0 is active at epoch 161 with 22 members. Native ordinary ownership is
active for 4,297 boards across eight profiles: Greenhouse 2,570, Ashby 934, Lever
194, Recruitee 114, Pinpoint 104, Teamtailor RSS 137, SuccessFactors RSS 197 and
Personio 47. All 7,885 enabled boards are preserved; four Python workers still
serve the remaining profiles.

The initial strict readback passed 127 completed deadline matches. The later
normal-schedule observation failed: 219 matched, but four completed SQL receipts
had earlier Redis retry scores after lease expiry. Fix the live Go reaper so a
committed receipt restores its canonical deadline before expanding ownership.
Seven profiles have native successes; Recruitee has no observed success in this
window. Bounded samples verified stored description hashes and upload flags,
without claiming remote object-byte or fleet-wide output/cost parity.

The source12 activation stopped at expired tokenless legacy work. Its original
pending identity was preserved while scheduled reconciliation finished. The
installed maintenance reaper restored 51 simple and six browser entries with
zero dead letters, missing configs or SQL ownership changes. Original pending
cancellation restored full readiness; original B0 rollback restored 22 members
and retired epoch 160. Exact selectors were cleared before the successful
source95 deployment. No lease/fence clearing or lock bypass was used.

## Continue delivery

1. Fix committed native receipt recovery in the normal lease reaper and prove
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

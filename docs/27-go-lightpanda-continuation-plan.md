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

Combined release **0.13.960** merged in [PR #10347](https://github.com/colophon-group/jobseek/pull/10347)
as source `acb4bb9e7627c541f7ff0aba4bb0c1ca4995f48b`. Its90 profiles add DOM rich
rows over direct/proxy/Lightpanda paths, HR Manager RSS and shared filters across
Generic RSS, SuccessFactors, Teamtailor and DOM. Required CI, installed contracts,
pilot and Crawler Deploy Gate passed; merge tree equals the reviewed tree.
Original [immutable deployment37570306419](https://github.com/colophon-group/jobseek/actions/runs/37570306419)
is in progress. Both images built; deployment and promotion must finish before
source960 ownership is authorized.

Supported source958 ordinary retirement, B0 rollback and selector clear finished.
Independent cold210 readback verifies the healthy exact-source958 base fleet,
cleared ownership and14 retained historical audit receipts. The previous958
ordinary plan is retired. Its last serving proof reached178 monitor and2 detail
completions across11 profiles, with one posting-write deadline diagnostic.
Its7,159 monitor boards and2,617 detail boards are historical cohort counts;
fresh source960 admission and actual posting routes remain required.

Candidate961 groups SuccessFactors legacy XML and structured Generic RSS
summaries (94 compiled profiles), faithful200-job stream prefixes, validated
Generic/WordPress pagination and raw browser response capture. Actual Python
references cover24 items,7 identities,9 failure streams,16 pagination configs
and13 cross-page traversal cases.18 canonical PostgreSQL/Redis worker cases and
39 cold cases pass. Raw XML/CDATA capture passed the existing installed
[Lightpanda pilot37571180241](https://github.com/colophon-group/jobseek/actions/runs/37571180241)
on amd64 and arm64. Affine/proxy pagination transport, remaining RSS properties,
full suites and deployment/adoption still remain.

Self-hosted Lightpanda uses the latest verified stable **1.0.0**, published
October2. Keep its verified immutable pin; recheck official releases when upgrading.
See the [worker checkpoint](30-native-ordinary-worker-checkpoint-2026-10-03.md)
and [grouped RSS evidence](evidence/go-native-rss-variant-core-candidate-2026-10-07.json).

## Continue delivery

1. Finish the original source960 deployment/promotion. Independently verify
   cold source/images, activate supported B0, capture a fresh source/receipt
   census and actual posting routes, and activate a new ordinary plan. Observe
   normal scheduled work, canonical fields, SQL/Redis deadlines, descriptions,
   publisher outcomes and health. Use supported recovery if activation fails.
2. Finish the grouped961 RSS batch with affine paginated transport and remaining
   enabled variants, then full suites, CI and source-bound rollout. Continue
   remaining DOM actions/pagination/empty-state/portal and monitor/detail/browser
   coverage in groups selected from the fresh canonical census.
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

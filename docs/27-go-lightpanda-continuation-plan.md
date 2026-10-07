# Go and Lightpanda migration delivery plan

Updated 2026-10-08. Delivery remains the full migration; it is incomplete.

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

## Latest checkpoint — 2026-10-08

Production runs **0.13.966**, source
`c3ca78862534353152294fa94e2e6ce5600c531f`, delivered through the grouped
[Deel/HiBob/TRAFFIT PR #10352](https://github.com/colophon-group/jobseek/pull/10352).
The original [crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37693870016)
succeeded on its deploy-only second attempt with the original immutable images.
Its first attempt correctly refused to deploy while the outgoing B0 owner existed.
The matching-source [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37695407644)
also succeeded. Independent cold verification passed at epoch **220**, with all
seven base services running and restart-armed, six HTTP endpoints healthy, no
native ownership/tokens, and preserved historical receipts.
**Lightpanda 1.0.0** remains the newest stable release, reverified October 8,
digest-pinned with CORS enabled.

The last broader native serving proof is source965 at epoch **219**, following
[PR #10351](https://github.com/colophon-group/jobseek/pull/10351).
It repaired the legacy ownership reader's API-browser classification and replaced
the deployment proxy preflight with the installed Go executable. All eight health
endpoints and ten running, restart-armed services passed before supported retirement.
Fresh source965 admission covered **7,295 monitor boards**, **2,616 detail boards**
and **1,482,240 scheduled postings**, with zero monitor configuration/cache
mismatches. Forty cached detail incompatibilities and one actual Eightfold route
retained legacy ownership. Three exclusive B0 boards remain outside ordinary
ownership. The captured remaining monitor census was **587**.

The grouped DOM/Inline/API-browser changes qualified 55 extra configurations.
A normal-schedule followup observed three newly admitted API-browser boards with
completed attempts and valid canonical descriptions; the 20 new DOM and two new
Inline boards still lacked natural completions in that snapshot. Full fleet
freshness and conservation remain unproved. The same followup recorded **77 claim
errors and 183 unacknowledged detail attempts**. Bounded read-only SQL observations
showed retained active detail fences and matching completed detail deadlines.
These failures remain open migration work; service health is not settlement proof.

Source966 ports **Deel, HiBob and TRAFFIT together**, covering all ten existing
registry configurations. Seventeen actual Python field cases, native HTTP/policy
fixtures, six real PostgreSQL/Redis write/policy scenarios and nine cold-retirement
scenarios passed. Required CI, actual Crawler Deploy Gate status and installed
image checks passed at reviewed head `710c7223be6b88560072d9c2d9ed8a8e3c901e36`.
The ten boards are **not yet native-owned**. The shared-action/detail-lock
correction in [PR #10354](https://github.com/colophon-group/jobseek/pull/10354)
merged as source967 `0f25771595d3caceab3e6c403f9cd66bdbd5d763` after final-head
Required CI and actual Crawler Deploy Gate passed. Its original
[crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37700033018)
is in progress; the matching
[renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37700256417)
has succeeded at this checkpoint. Broader native ownership stays inactive
until promotion and independent verification, then the combined profiles receive
fresh source-bound admission and one supported activation.
This avoids another activation of the reproduced lock-upgrade defect.

The merged correction adds shared DOM/Inline element and overlay
removal actions, retaining Python defaults, selector semantics, failure policy,
source binding and publisher checks. Full real PostgreSQL/Redis queue and worker
race suites passed (351.501s and
536.774s); actual pinned Lightpanda 1.0.0 service fixtures passed on both
architectures, as did installed image parity. The separate ARM64 candidate/control
measurement also passed. Fleet-wide service cost remains unproved.
The retained census qualifies six additional DOM configurations; the candidate also repairs a reproduced shared-board lock upgrade deadlock
affecting native detail profiles. Two real PostgreSQL detail validations failed
as unacknowledged before the correction; the corrected concurrent regression,
canonical/publisher revalidation and cold-retirement scenarios pass. Detail
attempts keep a shared configuration/policy lock and exclusive posting/fence
locks. Safe error-phase diagnostics are included for remaining failures.
Actual production error reduction remains unproved until deployed and observed.

## Continue delivery

1. Finish the original immutable source967 full-stack and matching renderer
   rollouts after merged PR #10354, then independently verify the promoted
   source/images and cold epoch220. The correction has passed the required
   checks; keep broader ownership inactive through promotion.
2. After exact-source promotion, activate/read back B0, then capture fresh
   canonical configurations, cache and actual posting routes. Stage and activate
   the resulting source/epoch-bound ordinary plan. Independently verify all
   service identities, readiness, ownership, normal scheduled completions,
   canonical content, publisher outcomes, freshness and queue conservation.
   Diagnose the high detail unacknowledged and claim-error counts through safe
   phase diagnostics; retain attempts and supported recovery authority.
3. Finish the next eArcu/CVWarehouse/Woowa group through full native suites,
   required CI, installed image identity and supported deployment. It covers
   all eight registry configurations, including both required eArcu proxies.
   Continue useful groups across remaining provider and shared browser contracts.
   The captured census includes DOM 219, API sniffer 116, RSS 17 and Sitemap 11.
   Shared DOM/Inline actions can qualify multiple types together; test actual
   Lightpanda behavior and preserve configured resource, proxy and browser
   requirements. Combine small shared additions with provider coverage instead
   of a separate runtime rollout for each option. Never weaken admission to reduce
   the count. Source966's ten new boards still need fresh serving evidence.
4. Replace mandatory Python runtime consumers: legacy worker/browser execution,
   schema preparation, supervision/reaper/metrics and operational entrypoints.
   Reuse delivered Go sync, queue, schema, reaper, drain and exporter engines;
   verify each actual installed deployment/maintenance consumer.
5. Prove comparable whole-service CPU/RAM/density/attributable cost, every-profile
   normal-schedule freshness/conservation, supported complete cold reversal and
   the rollback observation window. Synthetic B0 measurements alone are insufficient.
6. Remove production Python, Playwright, Chromium and runtime-only assets after
   all enabled profiles and consumers have replacement authority. Verify native
   image/startup/deployment/maintenance end to end. Preserve useful isolated
   offline Python tooling and every enabled board.

Maintain separate implementation, admission, ownership and natural-serving
status. Batch multiple compatible types per iteration. Add infrastructure or
fixtures for an observed failure or a changed contract. Keep the full delivery
goal active until all completion evidence and production retirement are done.

## Next grouped candidate — eArcu, CVWarehouse and Woowa

Candidate **0.13.968** adds four immutable profiles (115 total), covering eArcu's
three boards, CVWarehouse's two boards and all three Woowa variants. Both Aldi
eArcu boards retain required proxy authority and actual credentialed CONNECT;
no automatic direct fallback is introduced. This candidate is not yet deployed,
admitted or native-owned.

The port preserves eArcu live-only XML, bounded autodetection, retries and strict
same-portal vacancy URLs; CVWarehouse's largest advertised section, configured
locale first, localized job-ID deduplication and advertised-count validation;
and Woowa's stable pagination totals, detail identities, concurrency limit,
50,000-job truncation and provider source identities. Publisher reservations and
failed later pages/details cannot publish a successful partial inventory.

Twenty-seven actual Python field cases and all eight CSV queue admissions pass.
Focused complete-inventory, policy, retry and redirect fixtures pass. Eighteen
real PostgreSQL/Redis write/reservation/partial-failure scenarios passed, including
proxy execution; twelve real cold-retirement scenarios passed. The full queue
race run (360.334s) found one outdated registry expectation, corrected to require
explicit eArcu proxy authority; the focused registry/admission race retest passed
(1.627s). The full worker race suite and final-head CI remain pending.

Database verification caught and corrected two integration defects before
release: Woowa identities need provider-bound validation in the existing identity
writer; CVWarehouse root adverts (`https://tenant.cvw.io/?job=12`) were dropped
by the generic bare-host rule. The latter is an intentional correction to the
current Python worker's filtering: only HTTPS CVWarehouse root URLs with exactly
one numeric `job` query qualify. Generic root/navigation filtering remains.
Public-feed and normal-schedule production results are still required.

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

## Delivered four-provider implementation

Release **0.13.963** compiles 107 profiles with Manatal, HRMOS, Recruiterbox and
jobs.ch/jobup. It preserves
Manatal's advertised-count/no-progress rules and rich fields; HRMOS canonical
URLs, listing markers, explicit emptiness, totals and current-page checks;
Recruiterbox authoritative totals, partial-inventory protection and inactive
account evidence; JobCloud company aliases, portal/localized identities and
exact pagination completeness;
source-bound publisher observations and whole-inventory failure; and supported
cold retirement after interrupted writes or acknowledgment loss. The existing
queue, processors, persistence and maintenance paths remain the execution
contracts. See the [candidate evidence](evidence/go-native-provider-batch-six-candidate-2026-10-07.json).
Required checks, merge, immutable rollout, fresh admission, supported ordinary
activation and independent serving proof passed. New-provider completions and
full freshness/conservation proof remain pending at this checkpoint. Preserve every
remaining board until its replacement contract passes.

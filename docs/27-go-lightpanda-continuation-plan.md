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

The last independently verified production release is **0.13.965**, source
`0a6ddda170478cc6b6469615e086734940cf5fbf`, delivered through
[PR #10351](https://github.com/colophon-group/jobseek/pull/10351).
The original [crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37686292179)
and [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37686710589)
succeeded. **Lightpanda 1.0.0** is the newest stable release, reverified from the
official release list October 8. Its image remains digest-pinned, with CORS enabled.

Source965 repaired the legacy ownership reader's API-browser classification and
replaced the deployment proxy preflight with the installed Go executable. B0 and
broader Go ownership activated at epoch **219**, with independently verified
identities, all eight health endpoints and ten running, restart-armed services.
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

The next three-provider release **0.13.966** is merged as
`c3ca78862534353152294fa94e2e6ce5600c531f` in
[PR #10352](https://github.com/colophon-group/jobseek/pull/10352).
It ports **Deel, HiBob and TRAFFIT together**, covering all ten existing registry
configurations. Seventeen actual Python field cases, native HTTP/policy fixtures,
six real PostgreSQL/Redis write/policy scenarios and nine cold-retirement scenarios
passed. Required CI, actual Crawler Deploy Gate status and installed-image checks
passed at reviewed head `710c7223be6b88560072d9c2d9ed8a8e3c901e36`.
The original [immutable rollout](https://github.com/colophon-group/jobseek/actions/runs/37693870016)
is building. Supported outgoing source965 retirement is running and its writers
are quiesced. Source966 promotion, fresh admission and serving are still pending.
Do not count its ten boards as production-owned yet.

The following grouped work has begun on shared DOM/Inline element and overlay
removal actions, retaining Python defaults, selector semantics, failure policy,
source binding and publisher checks. Focused contract/worker/pilot tests pass.
The retained census qualifies six additional DOM configurations; more coverage
will join this candidate before a runtime rollout. Safe error-phase diagnostics
are included to make the existing claim/detail failures actionable.

## Continue delivery

1. Finish the supported source965 ordinary retirement, B0 rollback and selector
   clear. Independently verify the cold reversal, retained historical receipts,
   SQL/Redis ownership absence and restored base writers. Finish source966's
   original immutable crawler/renderer rollouts, preserving exact source/digests.
2. After exact-source promotion, activate/read back B0, then capture fresh
   canonical configurations, cache and actual posting routes. Stage and activate
   the resulting source/epoch-bound ordinary plan. Independently verify all
   service identities, readiness, ownership, normal scheduled completions,
   canonical content, publisher outcomes, freshness and queue conservation.
   Diagnose the high detail unacknowledged and claim-error counts through safe
   phase diagnostics; retain attempts and supported recovery authority.
3. Continue useful groups across remaining provider and shared browser contracts.
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

# Go and Lightpanda migration delivery plan

Updated 2026-10-07. Delivery remains the full migration; it is incomplete.

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

Production runs **0.13.964**, source `16ebb019d7b3dbafbf33bb829200369713a4da5c`,
merged in [PR #10350](https://github.com/colophon-group/jobseek/pull/10350).
The original [crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37668416367)
succeeded on its supported second attempt, retaining the original immutable
image pair. The [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37671318212)
also succeeded at that source. **Lightpanda 1.0.0** remains the latest stable
release, verified against the official release list October 7. The renderer
uses the pinned multi-architecture digest, with CORS enabled for CDP evaluation.

The grouped release qualifies **55 extra configurations across three types**:
33 API browser, 20 DOM and two Inline. Fresh canonical/cache and actual scheduled
posting admission found **7,295 monitors**, **2,616 detail boards** and
**1,475,517 scheduled postings** eligible for native ownership. It found zero
monitor configuration/cache mismatches. 40 cached detail incompatibilities and
one actual Eightfold route retain legacy ownership; three exclusive B0 boards
remain outside ordinary ownership. **587 monitor configurations remain** in the
captured census. These are admission counts, not current Go serving counts.

Broad ordinary activation at epoch 217 committed its exact SQL plan, then
failed legacy worker readiness: the installed Python projection reader omitted
`api_sniffer.browser-items/v1` from its browser profile classification. The
original wrapper contained all writers and retained its exact pending identity.
Supported `recover-pending` retired that plan and restored the complete
restart-armed base/B0 stack. All seven independent HTTP health probes passed;
nine services were independently verified running. **B0 remains active at 217;
broad ordinary ownership is withdrawn.** No direct SQL/Redis repairs occurred.

[PR #10351](https://github.com/colophon-group/jobseek/pull/10351), candidate
0.13.965 at `dd6f470bdb0e3fab4b6521a3e7ce3fc108b6a4b5`, repairs that reader and
moves the remaining deployment proxy preflight into the installed Go binary.
The old reader rejects the actual captured production plan; the patched reader
reproduces its exact Go projection. The shared Go/Python fixture now includes
API browser ownership. The installed-image codec and network-disabled native
preflight checks passed at this exact head. Required CI's native job stalled
in Ubuntu package-mirror setup before tests; its targeted retry has entered
execution tests. **The repair is not merged or deployed at this checkpoint.**
See the [reader correction](evidence/go-native-api-browser-legacy-reader-repair-2026-10-07.json).

The next grouped candidate ports **Deel, HiBob and TRAFFIT together**. It covers
all ten existing registry configurations and retains configured provider identity,
public request headers, settings resolution, pagination and publisher policy.
Seventeen frozen actual Python field cases and native HTTP fixtures pass.
Six real PostgreSQL/Redis write and publisher settlement scenarios pass; nine
cold-retirement scenarios pass across all three providers. This candidate is
not production-owned. Required release checks and fresh production admission
remain necessary; do not count the ten boards as delivered yet.

## Continue delivery

1. Finish exact-head required checks for #10351, re-read merge authority and
   deployment holds, then merge the repair. Use original immutable-image
   deployment. Before promotion, retire outgoing B0 with the original wrapper,
   clear exact selectors and independently verify the cold epoch, retired
   ordinary plan, preserved historical receipts, SQL/Redis ownership absence
   and restored base writers. Do not retry the incompatible source964 broad plan.
2. After exact-source promotion, activate/read back B0, then capture fresh
   canonical configurations, cache and actual posting routes. Stage and activate
   the resulting source/epoch-bound ordinary plan. Independently verify all
   service identities, readiness, ownership, normal scheduled completions,
   canonical content, publisher outcomes, freshness and queue conservation.
3. Complete the Deel/HiBob/TRAFFIT candidate through required CI and installed
   identity checks, merge and deploy with the same supported transitions.
   Continue remaining providers in useful groups. The current remaining census
   includes DOM 219, API sniffer 116, RSS 17 and Sitemap 11. Shared DOM/Inline
   click, wait_for, repeat and pagination contracts can qualify multiple types
   together; test actual Lightpanda behavior and preserve configured resource,
   proxy and browser requirements. Never weaken admission to reduce the count.
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

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

The eArcu/CVWarehouse/Woowa batch [PR #10355](https://github.com/colophon-group/jobseek/pull/10355)
merged reviewed head `affe3f9406ac55dac2a9e6316f84a5e3ff1e5191` as source968
`1676b322c6eced43601ee3ec3ed3ccd51edd850b`. Required CI, actual Crawler Deploy
Gate, native execution, installed-image parity and Lightpanda checks passed.
The original [crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37705811220)
has succeeded, including promotion, using its original build images. The matching
[renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37705841688)
has succeeded. Source968 native ownership remains inactive until independently
verified promotion. **Lightpanda 1.0.0** is still the newest stable release,
rechecked October 8, and remains digest-pinned with CORS enabled.

Outgoing source967 `0f25771595d3caceab3e6c403f9cd66bdbd5d763` completed the
supported ordinary retirement and B0 rollback. Independent cold verification
passed at epoch **222**: seven base services running and restart-armed, six HTTP
health endpoints, no native owners or tokens, selectors cleared, historical
write receipts preserved. The final source968 merge used that fresh closure
proof and exact head/base authority; no deployment hold was bypassed.

Before retirement, source967 admitted **7,311 monitor boards**, **2,616 detail
boards** and **1,483,867 scheduled postings**. Sixteen monitors were newly admitted:
three Deel, four HiBob, three TRAFFIT and six DOM. Forty detail-cache
incompatibilities and one actual Eightfold route retained legacy ownership;
three exclusive B0 boards remained outside ordinary ownership. The captured
remaining monitor census was **571**, including the incoming eight source968
provider configurations and twenty configurations in the next five-provider
candidate. These figures are admission evidence, not full fleet freshness.

Independent active-state checks passed for ten running, restart-armed services
and eight health endpoints. The bounded normal-schedule snapshot had zero claim
errors, 39 successful monitors and 47 successful details; three unacknowledged
details and one unacknowledged monitor remained, with safe settlement/failure
phase diagnostics. One newly admitted DOM board completed an attempt; new
Deel/HiBob/TRAFFIT normal completions were not yet observed. All 24 sampled
native-written descriptions had nonempty title/HTML/locales, exact matching
signed SHA256-prefix hashes, matching R2 pointers and completed uploads.
Full recovery, freshness and queue conservation remain open. Older monitor-only
samples contained two legacy description hash mismatches predating migration;
they are not evidence of a native description write.

Source967's shared DOM/Inline removal and overlay actions retain Python defaults,
selector semantics, failure policy and publisher checks. Its detail fix removes
a reproduced shared-board lock-upgrade deadlock while retaining exclusive
posting/fence locks. Full real PostgreSQL/Redis queue and worker race suites,
actual pinned Lightpanda service fixtures on both architectures and installed
image parity passed. The separate ARM64 candidate/control measurement passed;
comparable fleet-wide service cost remains unproved.

## Continue delivery

1. Finish source968's original immutable full-stack rollout and independently
   verify the promoted source, original image digests and cold epoch222. Preserve
   original failed-attempt evidence and use only supported recovery if needed.
2. Activate/read back source968 B0, then capture fresh canonical configurations,
   cache and actual posting routes. Stage and activate one source/epoch-bound
   ordinary plan. Verify service identities, readiness, normal scheduled work,
   canonical content, publisher outcomes, freshness and conservation. Diagnose
   remaining unacknowledged settlement phases using actual evidence; do not
   equate completed fences with successful work or health with settlement.
3. Complete the five-type Beehire/HireHive/WTTJ/Computrabajo/Y Combinator group,
   covering all twenty registry configurations and four required proxy routes.
   Then prioritize shared DOM/API/browser contracts with the largest remaining
   cohorts, combining compatible provider and action changes in useful batches.
   The source967 remaining census includes DOM213, API sniffer116, RSS17 and
   Sitemap11. Preserve resource, proxy, browser and complete-inventory contracts;
   never weaken admission to reduce the remaining count.
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

## Delivered implementation — eArcu, CVWarehouse and Woowa

Candidate **0.13.968** adds four immutable profiles (115 total), covering eArcu's
three boards, CVWarehouse's two boards and all three Woowa variants. Both Aldi
eArcu boards retain required proxy authority and actual credentialed CONNECT;
no automatic direct fallback is introduced. The implementation has merged; its original crawler rollout is pending and
production admission and native ownership are not yet established.

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
(1.627s). The full worker race suite passed (545.398s). Final-head native CI passed the
corrected complete queue suite; Required CI and actual Crawler Deploy Gate passed.

Database verification caught and corrected two integration defects before
release: Woowa identities need provider-bound validation in the existing identity
writer; CVWarehouse root adverts (`https://tenant.cvw.io/?job=12`) were dropped
by the generic bare-host rule. The latter is an intentional correction to the
current Python worker's filtering: only HTTPS CVWarehouse root URLs with exactly
one numeric `job` query qualify. Generic root/navigation filtering remains.
Public-feed and normal-schedule production results are still required.

## Next grouped candidate — five public hiring providers

Candidate **0.13.969** ports Beehire, HireHive, Welcome to the Jungle,
Computrabajo/PandaPe and Y Combinator together: twenty current configurations,
four required proxy routes and six immutable profiles (**121** total).
Implementation and focused local validation are complete; full suites, final-head
CI, installed images, production admission and native ownership remain pending.

Beehire preserves campaign-language selection, localized titles/locales, contract
codes, location fallback and incomplete/duplicate truncation. HireHive preserves
public pagination, tenant defaults, bounded retries and salary field parsing.
WTTJ retains anonymous search-only APIs, legacy organization hints, marketplace
mirror deduplication, ten concurrent detail requests, terminal-status filtering
and detail-closure handling. Computrabajo/PandaPe uses explicit totals and page
markers, 20-row pages, snapshot restart, bounded 403/429 retries and the existing
JSON-LD detail route; proxy owners cannot use direct egress. Y Combinator retains
scoped raw-HTML URL discovery and delegates posting fields to JSON-LD details.

Twenty-eight rich-field and eighteen listing cases match actual Python parsers.
All twenty CSV admissions pass. HTTP fixtures cover complete inventories,
late-page/detail failures, reservations, redirects, retries, gone semantics and
snapshot recovery. Eighteen real PostgreSQL/Redis scenarios verify canonical
writes, HTML staging, localized titles/locales, detail queue intents, reservation
and partial-failure settlement; a separate proxy execution guard passes.
Eighteen real cold-retirement scenarios pass, including interrupted writes,
commit-before-ack recovery and refusal of operator drift. Read-only Go HTTP probes succeeded on all sixteen direct configurations,
including live rich title/HTML/location/language samples and multi-page inventories
up to 2,760 jobs. Four required proxy configurations were deliberately not fetched
with a direct client; their credentialed proxy fixture and transport guard pass.
The full worker race suite is running; do not report full-suite or production
success yet.

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

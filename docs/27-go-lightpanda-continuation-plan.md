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

The five-provider [PR #10356](https://github.com/colophon-group/jobseek/pull/10356)
merged reviewed head `588dfebeff2034416a3c7cd7775252d30b556afa` as source969
`ce951b51a5afb1479f9a1ce7d07aeebd0adcf67e`. Required CI, actual Crawler Deploy
Gate, full native execution, browser/installed-image parity and Lightpanda checks
passed. Its original [crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37712020979)
and matching [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37712043127)
are running. Preserve those original build identities. Source969 native ownership
is inactive until the complete incoming release and fresh admission are verified.

Source968's supported ordinary retirement, B0 rollback and selector clearing
succeeded. Independent cold verification passed at epoch **224**: seven base
services running and restart-armed, six healthy endpoints, no native owners or
tokens and retained historical receipts. This fresh closure and exact head/base
checks preceded the source969 merge; no hold was bypassed.

Source968 [PR #10355](https://github.com/colophon-group/jobseek/pull/10355) merged
as `1676b322c6eced43601ee3ec3ed3ccd51edd850b`.
Its original crawler/renderer rollouts succeeded and native ownership was active
at epoch223 before retirement. It admitted 7,319 monitors, 2,616 detail boards and
1,479,882 scheduled postings. The latest normal-schedule snapshot had 23 successful
monitors, 54 successful details, zero claim errors and one unacknowledged monitor
settlement. The eight new eArcu/CVWarehouse/Woowa monitor completions had not yet
been observed; recovery, freshness and conservation remain open.

**Lightpanda 1.0.0** is still the newest stable release, rechecked October 8,
and remains digest-pinned with CORS enabled. The next shared DOM/API batch adds
eleven configuration contracts. Prefer one grouped native activation after both
compatible releases have green checks and independently verified deployments,
avoiding an extra ownership retirement between the five-provider and shared
proof batches. This groups seven monitor types and 31 configuration gains.

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

1. Finish source969's original immutable crawler and matching renderer rollouts;
   independently verify incoming images, source, readiness and cold epoch224.
   Publish the shared DOM/API candidate against the merged source and overlap its
   CI with rollout. If it is ready promptly, merge/deploy it while native ownership
   remains inactive, then perform one grouped fresh admission/activation.
2. Activate/read back incoming B0, capture fresh canonical configurations, cache
   and actual posting routes, then stage and activate one source/epoch-bound
   ordinary plan. Verify readiness, natural scheduled work, canonical fields,
   publisher outcomes, freshness and conservation. Source968 admitted 7,319
   monitors, 2,616 detail boards and 1,479,882 scheduled postings; its latest
   snapshot had 23 successful monitors, 54 successful details, zero claim errors
   and one unacknowledged monitor settlement. Full recovery remains open.
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
no automatic direct fallback is introduced. The implementation merged, both
original rollouts succeeded and production ownership was activated. Eight new
monitor configurations qualified; their natural completions had not yet been
observed in the bounded source968 snapshot.

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
Implementation, full native CI and installed-image/Lightpanda checks passed at
reviewed head `588dfebeff2034416a3c7cd7775252d30b556afa` in
[PR #10356](https://github.com/colophon-group/jobseek/pull/10356). Required CI and
the actual Crawler Deploy Gate are green. Exact-head merge and outgoing cold224 verification passed. The original incoming
deployments are running; production admission and serving proof remain pending.

Beehire preserves campaign-language selection, localized titles/locales, contract
codes, location fallback and incomplete/duplicate truncation. HireHive preserves
public pagination, tenant defaults, bounded retries and salary field parsing.
WTTJ retains anonymous search-only APIs, legacy organization hints, marketplace
mirror deduplication, ten concurrent detail requests, terminal-status filtering
and detail-closure handling. Computrabajo/PandaPe uses explicit totals and page
markers, 20-row pages, snapshot restart, bounded 403/429 retries and configured
DOM or JSON-LD detail routes; proxy owners cannot use direct egress. Y Combinator retains
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
The full local worker race suite passed (565.986s). Final-head CI passed the full
queue and worker suites, all fifteen configured DOM/JSON-LD detail bindings and
browser/installed-image checks. Production serving proof remains open.

## Next grouped candidate — shared DOM/API inventory proofs

Candidate **0.13.970** extends two existing monitor types together. It qualifies
nine current DOM configurations with `advertised_total`/`empty_states` and two
API sniffer configurations with typed `empty_response` markers. This is a
configuration-only screening gain of eleven; fresh route/cache admission and
production ownership are still required.

DOM checks retain exact advertised counts, whitespace/case rules, required and
forbidden links, full-match regular expressions and contradictory-marker
failure. API markers retain Python's exact scalar types, including distinct
`false`, `0` and `0.0`, and bounded missing-response retries. Unproved inventories
cannot settle as successful empty results. Combined DOM pagination or rich-row
proof configurations remain outside admission until their complete contracts
are verified.

Seventeen API and twenty-five DOM cases match actual Python behavior. Three
required-link regressions also retain the deployed Python 3.13 URL identity for
raw Unicode, percent escapes and query-only references. Fifteen
real PostgreSQL/Redis direct/rendered scenarios passed (8.427s): positive and
proved-empty settlement, malformed/missing inventories, reservations and 404
semantics. Both pure parser race suites and vet passed. The full queue race suite passed (377.626s); full worker race validation is
running. Candidate PR/installed-image validation and production authority remain
pending. Existing latest stable Lightpanda 1.0.0 and transport boundaries are
retained. Next prioritize compatible API filtering/convergence cohorts and
remaining DOM interactions in multi-type batches.

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

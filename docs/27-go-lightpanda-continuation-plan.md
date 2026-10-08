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

Release **0.13.971**, source `3ebd1d16cf8340670d4ac154ead7fedeb2e0ce65`,
is deployed from its original immutable crawler and matching renderer builds.
[PR #10358](https://github.com/colophon-group/jobseek/pull/10358) adds seventeen
API item/URL-filter contracts and eight TalentBrew boards, plus canonical proxy
selection for posting tasks whose Redis snapshot contains no board metadata.
Required CI, the actual Crawler Deploy Gate, installed native/browser validation
and Lightpanda checks passed at exact reviewed head
`c6901bf6b472a1fe0c1d1da26811a4c668bc38b5`. Fresh outgoing cold226 verification
passed immediately before the head-bound merge.

The original [crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37723339750)
and [matching renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37723373472)
succeeded. Independent checks on both hosts confirmed the exact source and image
digests. Incoming cold226 verification confirmed seven restart-armed base services,
six health endpoints, cleared selectors and no native ownership/tokens/one-offs.
Supported B0 activation and seven-endpoint readback passed at **epoch227**.
Fresh canonical configuration/cache and actual posting-route admission qualifies
**7,375 monitors**, **2,616 detail boards** and **1,470,796 scheduled postings**.
Forty detail-cache incompatibilities and one unsupported Eightfold route retain
legacy ownership; three B0 boards remain exclusive. Ordinary staging and supported activation passed at the unchanged epoch.
Independent serving verification confirmed all ten source-bound restart-armed
services and eight health endpoints. Its first bounded normal-schedule snapshot
had 23 successful monitor settlements, one successful detail settlement and zero
claim errors; SQL recorded 24 completed monitor attempts. New API/TalentBrew
monitor completions and fleet freshness/conservation remain pending. See the [source971 release evidence](evidence/go-native-source971-release-2026-10-08.json).

Source970's final ordinary activation had failed with `detail_transport:
configuration`. Its supported pending recovery, full-stack restoration, B0
rollback and selector clearing passed; all interrupted write receipts were
retained. The source971 fix uses the already-bound canonical detail profile to
select the proper sealed proxy client. Four real direct/proxy DOM/JSON-LD
regressions and the actual installed executable reproduced and verified the fix.
See the [historical recovery evidence](evidence/go-native-source970-cold-recovery-2026-10-08.json).

**Lightpanda 1.0.0** is the newest stable release, rechecked October8 against
[official releases](https://github.com/lightpanda-io/browser/releases/tag/1.0.0).
The production image pins
`sha256:5b84708cb3d9bef841aba4a4cd299f4de0609ac1bd7d4c6fbfcbf168d56b685e`
with CORS enabled; both architectures passed actual service fixtures.

The next grouped candidate is **0.13.972**: Intervieweb, Typify, Universia and
TalentReef together, covering **nine** enabled configurations and **126** installed
profiles. All nine public inventories passed; two TalentReef boards returned
verified empty brand-scoped inventories. All six delegated JSON-LD field samples
returned title, description and location. The four providers have actual Python
parity/component corpora, 28 verified HTTP fixtures, twelve real PostgreSQL/Redis
write/policy/failure cases and twelve real cold-retirement cases. Full queue (392.246s) and
worker (566.932s) real PostgreSQL/Redis race suites passed. Configuration screening against the fresh
source971 snapshot qualifies **7,384** monitors, leaving **498**; this candidate
has no production ownership. See the [candidate evidence](evidence/go-native-public-board-candidate-2026-10-08.json).

## Continue delivery

1. Source971 exact-plan ordinary activation and independent serving proof passed.
   Continue bounded normal-schedule observation and confirm
   canonical proxy detail execution, new API/TalentBrew monitor completions,
   description/R2 effects, failure recovery and queue conservation.
2. Publish the grouped four-provider candidate after its passed full local suites.
   Await native/installed CI and merge through fresh exact-head required checks.
   Use the original immutable deployment and supported cold cutover workflows.
3. Batch the largest remaining cohorts: **204 DOM**, **97 API**, **31 LinkedIn**,
   **17 RSS**, **11 Sitemap**, **9 Umantis**, **9 Unifr**, **8 Notion**, and other
   provider families. Preserve each enabled board's resource, proxy, browser,
   complete-inventory and content contracts. Reuse existing parsers and engines;
   admission must follow actual configuration and posting-route evidence.
4. Replace mandatory Python consumers: remaining worker/browser execution,
   schema preparation, supervision/reaper/metrics and operational entrypoints.
   Reuse delivered Go sync, queue, schema, reaper, drain and exporter engines;
   verify the actual installed deployment/maintenance consumers.
5. Prove every-profile normal-schedule freshness/conservation, comparable
   whole-service CPU/RAM/density/attributable cost, complete supported cold
   reversal and the rollback observation window.
6. Remove production Python, Playwright, Chromium and runtime-only assets after
   all enabled profiles and consumers have replacement authority. Verify native
   image/startup/deployment/maintenance end to end, retaining useful isolated
   offline Python tooling and every enabled board.

Maintain implementation, admission, ownership and natural-serving status. Port
multiple compatible types per iteration. Add infrastructure or fixtures for an
observed failure or changed contract. The full delivery goal remains active
until the completion evidence and production retirement are done.

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

## Delivered implementation — five public hiring providers

Candidate **0.13.969** ports Beehire, HireHive, Welcome to the Jungle,
Computrabajo/PandaPe and Y Combinator together: twenty current configurations,
four required proxy routes and six immutable profiles (**121** total).
Implementation, full native CI and installed-image/Lightpanda checks passed at
reviewed head `588dfebeff2034416a3c7cd7775252d30b556afa` in
[PR #10356](https://github.com/colophon-group/jobseek/pull/10356). Required CI and
the actual Crawler Deploy Gate are green. Exact-head merge and outgoing cold224 verification passed. The original incoming deployments succeeded; the source970 grouped admission
qualifies these monitors. Natural serving proof remains pending.

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

## Delivered implementation — shared DOM/API inventory proofs

Candidate **0.13.970** extends two existing monitor types together. It qualifies
nine current DOM configurations with `advertised_total`/`empty_states` and two
API sniffer configurations with typed `empty_response` markers. This is a
gain of eleven. Exact-source970 fresh route/cache admission and ordinary staging
passed; supported activation failed its final readiness gate; supported recovery, B0
rollback/selector clearing and independent cold226 verification passed.

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
semantics. Both pure parser race suites and vet passed. Full queue (377.626s) and worker
(555.425s) race validation passed. Final-head native, installed-image/browser,
required CI and deployment-gate checks passed; PR10357 merged and deployed. Existing latest stable Lightpanda 1.0.0 and transport boundaries are
retained. Next prioritize compatible API filtering/convergence cohorts and
remaining DOM interactions in multi-type batches.

## Next grouped candidate — API filtering and TalentBrew

Candidate **0.13.971** implements API `item_filter` include/exclude/regex/required
identities, global deduplication and locale preference before field projection.
The existing shared URL-policy writer applies post-discovery `url_filter` to
direct and rendered API inventories. Intentional removals adjust totals without
hiding upstream gaps; invalid required identities fail the complete cycle.
Existing fields, source boundaries, publisher reservations and separate detail
ownership remain intact.

TalentBrew preserves scoped HTML links, advertised totals, pagination/AJAX tenant
facets, fallback, retries, cookies, bounded same-origin career resources and
Python3.13 URL joining. Unicode decimal and underscored counters match Python;
oversized valid counters fail instead of dropping completeness evidence. Listing
URLs schedule the existing configured detail route (seven JSON-LD, one DOM).
No monitor titles or descriptions are invented from listing labels.

Source970 startup exposed a runtime dispatch bug: posting queue snapshots lack
canonical board metadata, so metadata-based selection sent a direct client to
an admitted proxy detail profile. Source971 now resolves the canonical bound
detail profile before selecting its sealed client; fetching/writes independently
revalidate it. Runtime monitor selection also includes eArcu/Computrabajo proxy
profiles. Real proxy DOM/JSON-LD tests reproduced the old failure and pass with
the fix. A native executable regression verifies an authenticated proxy request,
canonical title/HTML write, queue acknowledgment and graceful drain (5.311s).
Final-head installed-image CI runs that same regression.

Twenty-nine item-filter cases and eleven TalentBrew parse/AJAX cases match actual
Python. Eight direct/rendered API SQL/Redis scenarios (3.840s) verify title,
description, locations, publisher reservation, invalid identities and upstream
gaps; six TalentBrew SQL/Redis cases (4.945s) verify separate detail scheduling,
partial inventories, reservations, missing first pages and redirects. All25
registry contracts and all8 configured TalentBrew detail bindings pass. All8
public discovery probes and configured title/description/location field probes
pass. Parser race/vet passed, including late-page preference before projection. Full
queue (377.059s) and worker (564.539s) race suites passed; all six changed-contract
cold-retirement checks passed (6.513s), as did six bounded transient-status
retry scenarios (1.788s). The fresh full worker race suite passed with the observed runtime fix (561.220s).
Final-head CI and installed-image checks remain pending. See the
[candidate evidence](evidence/go-native-api-talentbrew-candidate-2026-10-08.json).

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

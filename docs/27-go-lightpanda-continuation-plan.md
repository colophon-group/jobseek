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

Release **0.13.975**, source `e08a42989ad65ce556d4d1538a959a56db5a57c3`,
is merged in [PR #10362](https://github.com/colophon-group/jobseek/pull/10362).
Required CI, the actual Crawler Deploy Gate and installed runtime checks passed
at head `b57489a0696134da29c153a6c9b0959c2fbaf42d`. The original
[crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37772644152)
and [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37772901951)
succeeded. Independent immutable-image, outgoing source974 cold234, incoming
source975 cold234 and B0 readback passed. B0 is active at **epoch235**; all seven
checked health endpoints passed.

This delivered batch compiles **132 profiles**: Unifr's nine monitors, PDF details
across fifty configurations, and seven Umantis receipt bindings. Sixty-one actual
Python Unifr cases, seventeen PDF field cases, seven binary/OCR cases and real
SQL/Redis settlement and retirement checks passed. Five public Unifr inventories
and field samples match; four existing source failures remain failures. Twenty
captured PDFs preserve title/location outcomes, including existing required-title
failures. PDF HTML/layout differs from pypdf; full description quality remains a
migration gate. See [source975 evidence](evidence/go-native-unifr-pdf-candidate-2026-10-08.json).

Fresh canonical/cache and actual posting-route admission at epoch235 screened
**1,494,003 scheduled detail postings** and selected **7,426 monitor boards**,
**2,672 detail boards**, and **1,463,368 scheduled postings**, including all fifty
PDF configurations and 162 scheduled PDF postings. Three monitor holdouts remain:
Hays, Siemens and RTX. Every board stays enabled. The independently checked fresh
plan is `9a517459c1dd1558abb5abf0670742533b15cbc7978a420a4c562cedc0d87745`,
projection `3355a6013b96b8bb9403767fde5b881e89fa57b0`. Supported ordinary activation
is waiting for natural legacy lease expiry. Production ordinary ownership is not
claimed at this checkpoint.

Source974's never-activated plan remains inert/staged. Its eligibility refusal
and subsequent supported recovery are retained evidence; current four-board UKG
config mismatches against that exact old plan warranted fresh admission. The
checks were retained, and the old plan is not reused.

## Next grouped candidate — SEEK and Avature

Candidate **0.13.976** compiles **134 profiles** and ports two monitor types across
**six SEEK and seven Avature boards**. SEEK drains the exact AU/NZ advertiser API
with stable total/page/advertiser/job-ID proofs. Avature preserves scoped portal
HTML inventories, explicit ranges and pagination, stable detail identities and
partial-inventory protection. Failed later pages expose no successful prefix;
publisher reservation is checked before status and parsing, including on 503.
Both monitors schedule the existing configured detail route without invented
listing titles or descriptions.

All thirteen captured public outcomes match Go: twelve complete inventories
(including an authoritative empty SEEK board), and HSBC's existing missing-range
failure. Twelve Avature detail samples match every original Python field through
the existing native DOM route. Public portal IDs 88 (HSBC) and 23 (Unifi UK) are
bound explicitly in the registry before admission. Settlement can confirm those
identities, including on truncated inventories, without rewriting the installed
ownership binding. Unbound or noncanonical identity configurations remain legacy
until qualified. SEEK GraphQL details still need their native replacement.

Ninety actual Python parser cases, thirteen registry checks, eighteen HTTP cases,
seven real SQL/Redis worker cases and six cold-retirement cases pass. The full
API race suite and queue/worker vet pass. Full queue/worker regressions and exact
candidate CI/rollout remain pending. See the
[grouped candidate evidence](evidence/go-native-seek-avature-candidate-2026-10-08.json).

Lightpanda remains pinned to stable **1.0.0**, verified against the
[official release](https://github.com/lightpanda-io/browser/releases/tag/1.0.0),
with immutable browser digest
`sha256:5b84708cb3d9bef841aba4a4cd299f4de0609ac1bd7d4c6fbfcbf168d56b685e`.

Source973's ordinary attempt failed when its B0 producer exited with
`redis/preflight`. Supported recovery restored the complete base/B0 stack and
retired that ordinary plan. Supported outgoing B0 rollback, selector clear and
independent cold232 passed before source974's rollout. Recovered Redis latency
never established the earlier failure's cause. Source974 adds bounded Redis
preflight/audit and bootstrap deadline diagnostics. Preserve the retired source973
plan as evidence; never reactivate it. See [source973 evidence](evidence/go-native-source973-release-2026-10-08.json).

### Prior source972 delivery

Release **0.13.972**, source `3c3231b6451316f3f861692ce572b7f82d60af8b`,
was deployed from its original immutable crawler and matching renderer builds.
Its supported ordinary retirement completed before source973’s rollout.
[PR #10359](https://github.com/colophon-group/jobseek/pull/10359) ports
Intervieweb, Typify, Universia and TalentReef together: nine configurations,
126 installed profiles. All nine public inventories and six delegated JSON-LD
field samples passed. Required CI, the actual Crawler Deploy Gate and installed
runtime/Lightpanda checks passed at reviewed head
`d9fb4274e4c4a0d15519de126be9722b77f3469c`.

The original [crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37731794665)
and [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37732069085)
succeeded. Independent renderer source/image verification and incoming cold228
verification passed. The first incoming cold check correctly refused an active
scheduled Go reconciliation; that job finished naturally before the successful
second check. Supported B0 activation and seven-endpoint readback passed at
**epoch229**.

Fresh canonical configuration/cache and actual posting-route admission qualifies
**7,384 monitors**, **2,616 detail boards** and **1,469,088 scheduled postings**
out of **1,496,376** scheduled routes. Forty detail-cache incompatibilities and
one unsupported Eightfold route retain legacy ownership; three B0 boards remain
exclusive. Supported ordinary staging passed at plan
`e1f5e762517c97ce664272e43181c81b654a20fc20dd9bb47753e8501de526ba`.
Supported ordinary activation and independent source/image/receipt checks passed:
all ten serving services are restart-armed and eight health endpoints are healthy.
The 06:19 UTC normal-schedule snapshot records fifty successful monitors and three
successful details, plus five claim errors unchanged from the initial snapshot.
The retained-fence follow-up has fifty-one completed recurring entries and one
active in-flight entry; the initial pending settlement recovered. This is bounded
recovery evidence, not complete fleet conservation. Newly admitted provider
completions and full natural freshness/description/R2 verification remain open.

Source972 also removes repeated full ownership-payload transfers from legacy
queue polling. Startup still fully attests immutable payload and Redis projection;
each claim freshly checks SQL plan/allocator identity under both barriers.
Fifty-eight real ownership tests and 63 pipeline tests pass. Source971's six
unacknowledged monitor settlements remain historical observations; retained
receipt inspection found no lost queue/lease entries in the first snapshot and
normal recovery progress in the follow-up. This does not establish causation or
complete fleet conservation. The migration remains incomplete.

**Lightpanda 1.0.0** is the newest stable release, rechecked October8 against
[official releases](https://github.com/lightpanda-io/browser/releases/tag/1.0.0).
Production pins
`sha256:5b84708cb3d9bef841aba4a4cd299f4de0609ac1bd7d4c6fbfcbf168d56b685e`
with CORS enabled; both architectures passed actual service fixtures.

The delivered shared-variant release is **0.13.973**,
[PR #10360](https://github.com/colophon-group/jobseek/pull/10360): DOM rendered
roots with HTTP pagination tails, explicit Sitemap roots/content retries and
SuccessFactors RSS company/required property enrichment. It qualifies nineteen
additional configurations (eleven DOM, six Sitemap, two RSS). Thirty-two real
PostgreSQL/Redis cases and actual Python metadata/text corpora pass; full queue
(380.958s) and worker (596.328s) race suites passed before the final Sitemap
budget correction. Full Required CI and installed-runtime checks passed at
`daf3c288251c0318297217c1c334ae03f1312d66`.

Public inventories include eight Cyberbit jobs, 101 US/25 Europe NetJets jobs
with company/title/description/location fields, and M6's verified empty filtered
inventory. The Jobteaser probe exposed the old 55 MiB total byte limit: six job
leaves total approximately 93 MB. Retaining 50 MiB per file and a bounded 512 MiB
operation produces twenty-one filtered jobs, matching actual Python's public
inventory. Budget exhaustion regressions and changed real DOM/Sitemap settlement
cases pass. Public Lightpanda probes completed Bank of China (254), Castelion
(90) and Deloitte CE (485); Celebal, Cartier, Tesco Bank and Boeing BIA also pass; four DOM monitor holdouts remain.
Protected proxy probes match Barclays Python/Go inventories (765). L’Oreal
returns zero after filtering in both runtimes; its configured source points at
PR rather than en_US jobs. RTX succeeds in Python but fails in Go; diagnosis
is required before ownership expansion. Configuration screening grants no
production ownership.

The same candidate switches Kaiser’s main board to existing TalentBrew discovery
and direct JSON-LD details, and Euronext’s WTTJ board to its existing rich API
monitor with skip details. Actual Python and Go both find 3,091 Kaiser jobs and
one Euronext posting; three direct Kaiser samples return title, description and
location. Current CSV queue admission checks pass (1.615s). These are candidate
configuration changes, without production ownership; see the
[route evidence](evidence/go-native-existing-provider-route-candidate-2026-10-08.json).

## Continue delivery

1. Complete grouped974 PR #10361 Required CI and the actual Crawler Deploy Gate. Merge with fresh
   unchanged head/base, no holds and exact-head authority. Keep Lightpanda on the
   newest verified stable release and immutable digest.
2. Quiesce and retire outgoing B0 through supported wrappers, prove cold state,
   then install original merged-source crawler and matching renderer images.
   Build fresh archived-source configuration/cache/posting-route admission;
   perform supported B0 and ordinary staging/activation and independently verify
   every service, receipt and endpoint. Investigate any producer failure using
   safe error-family diagnostics; preserve ownership and publisher checks.
3. Port Unifr with PDF details next, then group remaining DOM/API/RSS/Sitemap/
   LinkedIn contracts by reusable engines. Observe normal-schedule fields,
   description/R2 effects, settlement recovery and freshness as ownership grows.
4. Replace remaining mandatory Python runtime/deployment/maintenance consumers.
   Existing sync, queue, schema, reaper, drain, exporter and reconciliation engines
   are already Go; verify actual installed consumers before duplicating work.
5. Prove every-profile natural freshness/conservation, comparable whole-service
   CPU/RAM/density/attributable cost and supported cold reversal/observation window.
6. Remove production Python, Playwright, Chromium and runtime-only assets after
   all enabled profiles and consumers have replacement authority. Retain useful
   isolated offline Python tooling and every enabled board.

Port multiple compatible types per iteration. Add infrastructure or fixtures for
an observed failure or changed contract. Keep the full delivery goal active until
completion evidence and production retirement are done.

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

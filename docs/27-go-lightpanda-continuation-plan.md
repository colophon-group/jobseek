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

Release **0.13.976**, source `072160c56d744a84933097cd184a39cd37c5d9c9`,
merged in [PR #10363](https://github.com/colophon-group/jobseek/pull/10363)
after Required CI, the actual Crawler Deploy Gate and installed-image checks
passed at head `959a8c32730f9d4f2f571f948def3852b6791abb`. The original
[crawler rollout](https://github.com/colophon-group/jobseek/actions/runs/37790063521)
and matching [renderer rollout](https://github.com/colophon-group/jobseek/actions/runs/37790138361)
succeeded. Independent source/image and incoming cold236 checks passed.
B0 activation and seven health endpoints passed at **epoch237**.

This batch compiles **134 profiles** and delivers six SEEK and seven Avature
monitors. Ninety actual Python cases, thirteen registry checks, eighteen HTTP
cases, seven real SQL/Redis worker cases and six cold-retirement cases pass.
All thirteen public inventory outcomes match, including one empty SEEK board
and HSBC's existing missing-range failure. Twelve Avature detail samples match
every original Python field through the existing native DOM route. Explicit
portal IDs bind HSBC and Unifi UK. SEEK details still use Python in this release.

Fresh epoch237 admission screened **1,492,188 scheduled detail postings** and
selected **7,439 monitors**, **2,672 detail boards** and **1,461,672 scheduled
postings**. All thirteen new monitors are included. Three monitor holdouts,
41 detail cache exclusions and one actual-route exclusion remain; every board
stays enabled. Exact plan
`11d3df89631b635314d1b4914ccdaf043026a3df1a1f5ad6854cb473536b8b3e`
and projection `22ee5efd7feb03e8acfd8b4ab1049ab86158fe68` were activated and
independently verified at epoch237: all ten immutable-image writers, eight health
endpoints and SQL/Redis ownership passed. The 15:05 UTC natural sample records
165 successful monitors, three successful details and zero claim errors; failed
and gone outcomes remain separate. Supported ordinary retirement, independent
full base/B0 restoration, B0 rollback/selector clearing and cold238 subsequently
passed in preparation for source977. No native ownership remains active at this
checkpoint; the complete source976 base stack is restored. See
[source976 evidence](evidence/go-native-seek-avature-candidate-2026-10-08.json).

The prior source975 owner was retired through the original wrapper. Independent
full base/B0 restoration, supported B0 rollback/selector clearing and outgoing
cold236 checks passed before merge. Source974's never-activated plan stays inert.
Source975 delivered nine Unifr monitors, fifty PDF configurations and seven
Umantis receipt bindings; its 293 successful monitor and seven successful detail
observations remain samples, not full freshness/conservation proof. PDF HTML/layout
still differs from pypdf; description quality remains a migration gate.

## Next grouped candidate — SEEK details and automatic API arrays

Candidate **0.13.977** compiles **135 profiles**. It replaces SEEK GraphQL details
for all six current boards and extends the existing API monitor for eleven
configurations with explicit field and URL mappings and automatic array selection.
SEEK advertiser and AU/NZ market bindings come from the canonical board; endpoint,
query, credential and transport overrides are rejected. Publisher reservations
stop status handling, parsing and retries, including 503 and incomplete bodies.
Expired/empty results keep the existing transient failure and monitor-delisting
contract. API selection preserves ordered traversal, scores and stable ties;
resource limits discard all candidates. Literal empty arrays or exact configured
empty documents can prove absence; unproved empty/small wrappers fail.

Twenty-five actual Python SEEK cases, fifty-eight array cases, ten inventory
cases, seventeen registry bindings, sixteen HTTP cases, fifteen real SQL/Redis
worker cases, six interruption/retirement cases and a retained-receipt fresh-epoch
case pass. Full API (5.288s), queue (936.523s) and worker (919.991s) race suites,
vet, Python ownership/cutover (78 passed, 9 skipped), and 126 workflow/version
checks pass. Ten public SEEK outcomes match every populated field and GraphQL
request: eight populated and two empty results. Eleven public API captures match
106 postings and two empty inventories, with empty/absent metadata and extras
objects normalized as absent; raw differences are retained. Required CI, actual
deploy gate and installed-image checks passed at head `edb0322cfb15ea855c747d03794cc7b4635f3ffc`.
Fresh merge authority correctly stopped when `main` advanced with web-only
PR10364. The candidate is rebased on `56b32385877c622f7e188e64a1a0d8afbf4d07ab`;
new exact-head checks and production delivery remain pending. Crawler code is
unchanged by that rebase; historical test identities are preserved. See
[next candidate evidence](evidence/go-native-seek-detail-api-array-candidate-2026-10-08.json).

The following grouped port targets all 31 LinkedIn, four Taleo and four
PracticeMatch monitors. Its separate 0.13.978 prototype compiles 138 profiles
and has no production authority. Actual Python cases pass (68 LinkedIn, 61
Taleo, 26 PracticeMatch), with 39 registry bindings, 39 HTTP cases, eleven
PostgreSQL/Redis cases and three family cold-retirement cases. Current public
qualification is running; broad checks remain pending. LinkedIn detail fields
retain their existing delegated schedule. Full migration completion gates remain open.

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

1. Complete SEEK detail/API PR #10365 Required CI and the actual Crawler Deploy Gate at its refreshed head. Merge with fresh
   unchanged head/base, no holds and exact-head authority. Keep Lightpanda on the
   newest verified stable release and immutable digest.
2. Quiesce and retire outgoing B0 through supported wrappers, prove cold state,
   then install original merged-source crawler and matching renderer images.
   Build fresh archived-source configuration/cache/posting-route admission;
   perform supported B0 and ordinary staging/activation and independently verify
   every service, receipt and endpoint. Investigate any producer failure using
   safe error-family diagnostics; preserve ownership and publisher checks.
3. Finish the grouped LinkedIn/Taleo/PracticeMatch monitors, then the remaining
   detail and browser contracts by reusable engines. Observe normal-schedule fields,
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


## Next grouped detail batch: LinkedIn, JazzHR, Taleo Enterprise

Delivered release **0.13.979** adds three independent detail profiles for **59 current
configurations**: 34 LinkedIn, 20 JazzHR, and five Taleo Enterprise. It reuses
the existing JSON-LD and DOM libraries and preserves provider-specific HTTP
retries, selected enrichment fields, exact closed-job signals and bounded public
redirects. All 51 original Python parser cases, 16 source identities, 63 actual
Python request traces and 30 additional publisher/redirect cases pass. The 59
registry bindings pass. Same-capture public replay matches all fields and request
counts for 51 outcomes across 48 boards: 45 populated results and six actual
Python empty results. Eleven boards have no active scheduled posting to sample.

All 17 real database settlement cases and six document-history/cold reversal
cases pass, including all three new detail profiles. The preceding three-monitor
batch also passed its full queue (439.568s) and worker (649.559s) race suites.
Both batches shipped together in PR #10366 as **0.13.979**, with six new
profiles and 98 configurations. Combined queue/worker race suites passed
(439.190s/675.551s), followed by exact-head Required CI, Crawler Deploy Gate,
installed image parity, and ARM whole-lane admission. The original full crawler
rollout `37816859534` and renderer rollout `37816935520` succeeded at source
`5b41c1fd4cc5b80322b4e4c90a6630ab4156e798`. Independent readback confirmed the
complete healthy immutable base stack at cold epoch 240. Fresh ordinary admission selected 7489 monitors and 2738 detail boards at
epoch 241. Activation failed after legacy UKG discovery added equivalent resolved
aliases to Co-op and Ollie's between preflight and quiescence. Supported recovery
and independent verification restored all nine base/B0 writers and seven health
endpoints; ordinary ownership remains inactive and the failed plan remains inert.
Full natural freshness remains pending. See the [candidate evidence](evidence/go-native-static-provider-details-candidate-2026-10-08.json).
Next address reusable DOM/API configuration gaps in groups: the retained
source976 offline census found 193 unsupported DOM monitors and 86 API monitors.
Those counts are prioritization evidence, not production ownership or freshness
proof. Keep remaining boards enabled and preserve useful offline Python.


## Next grouped monitor batch: 104 Job Bank, CNStaff, SeamlessHiring

Merged **0.13.980** adds four profiles across three providers and all six
current registry configurations. The three proxy-required 104 boards retain
sealed proxy routing; the fourth uses direct HTTP. CNStaff and SeamlessHiring
retain their complete public API inventories, including authoritative empty
results, mandatory pagination checks, partial-result protection, existing retry
budgets, publisher precedence and rich fields. A configured detail scraper
does not force enrichment when the original rich processor used feed content.

All 51 original Python inventory cases and 87 original HTTP traces match.
Twenty-one publisher/redirect cases, six registry bindings, fourteen real
PostgreSQL/Redis write/settlement/transport cases and twelve supported cold
retirement cases pass. API full race and both API/worker vet checks pass.
The initial batch head passed full queue/worker race suites
(455.589s/652.516s), Required CI and installed image parity. The revised batch also
fixes the observed UKG cutover blocker: ownership hashes resolve host, tenant,
board ID and listing URL before legacy discovery persists equivalent aliases.
Conflicting aliases, changed targets, parser settings and unrelated configuration
remain bound. Ten reference binding cases plus the changed-parser fence and
thirteen real activation/retirement cases pass, including monitor and independent
detail admission, conservation and rejection before effects. Revised full queue/worker race suites passed (454.409s/673.007s), followed by
exact-head Required CI, Crawler Deploy Gate, installed image parity and amd64/arm64
pilot checks. PR #10367 merged at source
`ee4d6d27f6806b22a1ec86e05c9d504364e239a1` after supported outgoing B0
retirement, selector clearing and independent complete cold242 proof. Original
crawler rollout `37827765892` and matching renderer rollout `37827922155`
succeeded on attempt 1. Independent full incoming cold242 proof passed; supported
B0 activation and readback passed at epoch243. Fresh ordinary admission and
activation remain pending. See the
[candidate evidence](evidence/go-native-small-provider-candidate-2026-10-08.json).

The source977 ordinary activation failure is retained: UKG resolved and saved
`metadata.listing_url` after staging, so its monitor and detail hashes differed
from current canonical/cache bindings. Supported recovery restored every writer.
The stale plan remains inert history. Source979 repeated the same failure after a zero-drift preflight because CSV sync
removes the learned aliases. The source980 semantic binding correction addresses
that cause; rebuild admission on the exact deployed revision. Preserve target and
configuration fences. Do not retry an old-source plan.

Continue with grouped DOM/API configuration gaps, and qualify 51job/Jarvi
together where their current board contracts permit. Keep the full migration
goal active through all enabled monitor/detail/browser coverage, mandatory
consumers, full freshness/conservation, comparable cost, supported reversal and
the retirement window. Remove production Python/Playwright/Chromium only after
those replacement gates pass; preserve useful offline Python tooling.


## Next grouped shared API and DOM configuration batch

Candidate **0.13.981** extends the existing 145 profiles for **35 current registry
configurations**: 19 automatic API field mappings, four explicit API corrections
and 12 DOM direct-board/JSON-LD
verification configurations. The original direct, required-proxy and rendered
transports remain bound. Twelve other API configurations and eleven DOM
configurations with these flags retain their existing owner because additional
options remain unsupported. These are configuration qualifications, not production
route or output claims.

Automatic mapping collects complete filtered/paginated inventory before inspecting
the first five rows, including ADP location/name-code and team mappings. An
ambiguous candidate field fails the whole cycle and requires explicit mapping;
URL-only inventories preserve their distinct processing path through both HTTP
and rendered replay. DOM includes the configured board after successful discovery
and preserves the original downstream URL classification. JSON-LD verification
uses full detail bodies, the existing 500-URL cap, eight bounded concurrent
requests, original retry/omission semantics and cancellation/drain on failure.
A publisher signal takes precedence and binds the exact observed child to the
original listing, configuration and claim.

Thirty-two original Python mapping cases, four ambiguity rejections, seventeen
original full API HTTP cases and sixteen original DOM HTTP/processing cases pass.
The 35 registry bindings preserve all declared transports. Twenty-three real
PostgreSQL/Redis direct/proxy/rendered write/settlement cases and twenty-seven
supported cold retirement cases pass. API/DOM full race and API/DOM/worker vet
pass. Full initial-head queue/worker race suites passed (478.205s/684.571s),
followed by Required CI and the actual Crawler Deploy Gate. Same-capture Go HTTP
replay matches every field and request shape/count for all 22 successful original
API captures. Four ambiguous publisher payloads require explicit CSV mappings;
these preserve all 81 captured jobs and fields under actual original Python and
Go replay. Bucher text comparison applies the original processing normalization.
One original HTTP reference failed (EasyJet Taleo); affine browser and DOM public
qualification, revised-head CI, immutable deployment and fresh route admission
remain pending. See the
[candidate evidence](evidence/go-native-shared-dom-api-candidate-2026-10-08.json).

Continue in groups across remaining shared browser/pagination/verification options
and provider types. Keep the full migration goal active until coverage, mandatory
consumers, full natural freshness and queue conservation, comparable cost, supported
reversal and the retirement window justify removing production Python, Playwright
and Chromium. Preserve useful isolated offline Python and every enabled board.

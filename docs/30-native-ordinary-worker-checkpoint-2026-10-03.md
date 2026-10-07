# Native ordinary worker continuation, 2026-10-03

## Current delivery — 2026-10-07

The full migration goal is **active and incomplete**. Ship multiple provider
and variant types per release, sharing reference verification, CI and cutover.

### Next grouped RSS parser core candidate961 — October7

The next grouped batch has native SuccessFactors legacy XML and Generic RSS
structured-summary parser cores. Twenty-four actual Python item cases and seven
strict feed-identity cases pass under the race detector in1.790 seconds. They
cover leading XML text, duplicate/namespaced children, locations and remote type,
Unicode IDs, tenant query identity, summary title/location/employment fields and
malformed inventories. No native ownership profile or discovery route is added
by this core checkpoint. Complete configured fetch/pagination/browser paths,
canonical writes, cold reversal and installed-image verification before adoption.
Keep WordPress RSS and remaining SuccessFactors detail properties in this batch.

### Combined delivery update — 03:32 UTC October7

PR #10347 now groups DOM direct/proxy/Lightpanda rich rows, HR Manager RSS and
shared filters for Generic RSS, SuccessFactors, Teamtailor, rich DOM and URL-only
DOM in release0.13.960. The complete worker/queue race suites passed at clean
`0444d5c2a3b02de7d074b1168bf500c7cd9aeaff` in502.851/301.919 seconds.
The combined crawler tree is identical; CI additionally uses a bounded20-minute
suite deadline after the slower runner hit Go's ten-minute default. Fresh exact-
head required, native and installed-image checks remain required. One combined
PR and deployment replaces separate959/960 deliveries.

Original release958 deployment37564684440 and promotion succeeded. Independent
readback verified exact image digests, full base health and cold208; supported
B0 activation then succeeded and source/receipt-bound readback passed at209.
Fresh admission reconciles1,493,765 scheduled postings and admits7,159 monitor
boards and2,617 detail boards with1,468,186 scheduled owned postings. Forty
detail boards retain their legacy owner because actual routes/cache did not
match. Ordinary staging is running; actual ordinary serving/natural processing
is not yet claimed by this checkpoint. Lightpanda remains pinned to1.0.0.

### Grouped shared policy candidate960 — October7

Candidate960 ports shared rich-job filters and provider URL allowlists across
Generic RSS, SuccessFactors RSS, Teamtailor RSS, DOM rich rows and DOM URL-only
monitors. Twenty-six actual Python policy references (including two malformed
allowlists refused before ownership), four actual Python RSS stream references,
26 PostgreSQL/Redis cases and33 cold-retirement cases pass. The native path
preserves raw duplicate ordering, filter order, Python metadata text, allowlist
fullmatch, accepted writes on boundary rejection, and the original200-job RSS
batch prefix when a later classification fails. Failed cycles cannot finalize
empty/missing/gone processing; URL-only jobs retain independent detail work.

An offline comparison of21 policy-bearing boards in the unchanged source957
canonical snapshot admits eight configurations across these five families;
parent959 admitted none. Thirteen still require other options. This snapshot
proves eligibility only. Full worker/queue suites, immutable-image CI, fresh
canonical admission and actual production ownership remain required. See the
[portable candidate evidence](evidence/go-native-shared-feed-policy-candidate-2026-10-07.json).

PR #10347 is published at `42feb0eb32f8ab29d698d3169e4e6b8e58bad4c1` for the
completed DOM rich-row and HR Manager batch. Its full local worker/queue suites
passed in499.379/282.912 seconds. Original CI is running; the amd64 Lightpanda
pilot failed on a public Go module-proxy download and only that failed job was
rerun. PR #10346 is merged at `82c73de3ea432634c5e365c5f57dfc02873a4bf6`;
its original immutable release build passed and full deployment37564684440 is
running. Source958 production success and ownership are not yet claimed.
Lightpanda remains pinned to the latest verified stable release1.0.0.

### Delivery update — 03:04 UTC October7

[PR #10346](https://github.com/colophon-group/jobseek/pull/10346) merged as
`82c73de3ea432634c5e365c5f57dfc02873a4bf6` after fresh exact-head/base/draft/
operator/hold/required-check authority and cold208 readback. Its parent is the
expected source957 revision and its tree exactly equals reviewed head0a4bc8e.
The original [immutable deployment37564684440](https://github.com/colophon-group/jobseek/actions/runs/37564684440)
is live; preflight passed and build/company-OG jobs are running. Production is
not yet claimed to be source958. Retain original run identity, prove the full
rollout and exact images, then independent cold readback and fresh B0/ordinary
source/receipt-bound staging, activation and natural canonical/queue proof.

Candidate959's full worker race suite passed in499.379 seconds; the full queue
suite now passes in282.912 seconds with its scoped cursor assertion retained.
The index-test fixture change affects no production runtime code. Candidate959
is rebased onto merged958 with an identical tree to its full-suite source. Its
shared transport/parser/persistence/cold tests and Python ownership proof are
complete locally; publish this grouped DOM/HR Manager PR and require fresh
immutable-image/native CI and Crawler Deploy Gate before delivery. Keep the
production ownership cutover separate from this local candidate evidence.

### Full-suite follow-up — 02:51 UTC October7

Candidate959's full worker race suite passed in499.379 seconds at
`57a457cc6cd07bfaebd275ffde21db475a5d10a7`. Its full queue suite found one
existing cursor-plan assertion failure: the tiny private database chose the
primary-key index rather than `idx_jp_board_id_cursor`. The test now seeds2048
competing-board rows inside a rolled-back transaction and analyzes that private
fixture. It still requires the exact scoped UUID index with no sort; five
repetitions pass in2.493 seconds. No production runtime code changed in this
follow-up. Re-run the full queue suite and retain its original failed evidence.
Candidate958's original browser-image CI build is still live; do not merge it
until Required CI is terminal green and fresh final merge authority is checked.

### Grouped candidate959 implementation — 02:36 UTC October7

Candidate959 now integrates DOM rich rows across direct HTTP, credentialed proxy
HTTP and Lightpanda rendered single pages, plus HR Manager RSS. The executable
reports90 compiled profiles. Fifty-two actual Python DOM reference cases,
17 actual Python HR Manager join cases,39 PostgreSQL/Redis worker cases and
18 cold-retirement cases pass. The cold cases include direct/proxy/rendered DOM
rows and HR Manager, with interrupted claims, committed-before-ack receipts,
config drift, and Redis restart. Python ownership attestation passes42 cases
with six environment-dependent skips. Full DOM race tests, focused worker/queue
race tests, formatting and module dependency checks pass; full worker/queue
suites and immutable-image CI remain required before merge or deployment.

The native DOM path preserves complete first-page rich HTML, first raw URL
across pagination, Python URL joining even for rendered rich rows, ordered
replacement ownership hashes, missing-content detail recovery, future detail
deadlines, and scraper-owned descriptions/locales. HR Manager binds the first-
party tenant board and feed, validates exact position/feed IDs, persists durable
source identity and structured locations/employment/language, and attributes
publisher reservations to the observed board or feed resource.

An offline screen of the unchanged source957 canonical census admits18 direct
DOM rich-row boards and one HR Manager board. Ten of28 DOM rich-row boards remain
refused because they require additional options, including browser pagination,
empty states, prospective canonical paths, job filters and portal-specific
behavior. Pure parsing of all28 rich_rows objects is narrower evidence than
whole-profile eligibility. No candidate959 production ownership is active.
Continue these remaining variants and RSS extensions in subsequent grouped
ports; preserve every currently enabled legacy route until its replacement is
proved. Candidate958's original Required CI browser-image build is still live.

### Latest operational update — 02:10 UTC October7

Production source957 is now independently verified **cold at epoch208** after
supported B0 rollback and selector clear. Both native ownership paths, receipts,
projection, claim tokens and current write fences are absent. The failed957 plan
is retired; historical audit receipts remain intact. Seven base processes retain
exact release images and restart policies, six serving health endpoints return200,
B0 producer/executor are stopped under their supported policies, and no one-offs
remain. This supersedes the earlier active207 readback below.

[PR #10346](https://github.com/colophon-group/jobseek/pull/10346), candidate958,
is ready at exact head `0a4bc8efc598772602ddbdfe4c33d2d93403e34e`.
Full local worker/queue race suites passed in492.648/271.430 seconds with required
PostgreSQL/Redis fixtures. Original native execution CI, installed-image parity
for all85 profiles and Crawler Deploy Gate passed. Original
[Required CI run37558109978](https://github.com/colophon-group/jobseek/actions/runs/37558109978)
is still finishing its browser-image build at this checkpoint. Merge requires
its terminal success and a fresh head/base/operator/hold/check/merge-state audit.
A new immutable deployment, source/receipt-bound census and newly staged ordinary
plan must follow. Neither the retired957 plan nor an earlier cohort grants reuse
or natural-processing authority.

The next candidate959 has a committed native DOM rich-row configuration and
extraction core. Fifty actual Python reference cases and the full DOM module
race suite pass; all28 current canonical rich-row configs parse unchanged.
Direct-child relational selectors, replacement order, descriptions, locations,
section/lifecycle bounds, duplicate policy and advertised counts are covered.
This proves the pure parser. Ordinary direct/proxy/rendered discovery profiles,
delegated field scheduling, canonical persistence and cold reversal still need
integration and verification before any board is adopted. Keep remaining RSS
variants in this grouped continuation and update Python browser ownership
attestation when introducing a rendered rich-row profile.

### Prior source957 and candidate958 snapshot

Production **0.13.957** is deployed from merged source
`60d0de8bebfbd25486791cb741d3b5ce814dfbce` through the successful original
[deployment 37553965143](https://github.com/colophon-group/jobseek/actions/runs/37553965143).
[PR #10345](https://github.com/colophon-group/jobseek/pull/10345) delivered ten
profiles together: proxy monitors for DOM, API-sniffer, Inline, Sitemap,
Eightfold and Phenom; proxy details for Eightfold, DOM, JSON-LD and API-sniffer.
It also fixed the ordinary wrapper's proxy-pool rendering to match the supported
B0 workflow. Both required checks and installed-image parity passed against the
exact reviewed head. Superseded [PR #10344](https://github.com/colophon-group/jobseek/pull/10344)
is closed. There are now 81 installed profiles.

The original supported B0 activation and independent readback succeeded at
**epoch 207**, with exact source/image identities and seven healthy endpoints.
The first readback had no completed B0 executions; natural processing remains
unproven by that observation.

Fresh, read-only source-bound admission reconciled **1,496,554** scheduled
postings against their actual routes. It admits **7,157 monitor boards** and
**2,617 detail boards**, covering **1,470,928 scheduled postings**. Three fixed
B0 boards are excluded from ordinary ownership. Thirty-nine detail boards with
cache/lane discrepancies and one with an unsupported actual route retain their
existing owner. Canonical configuration hashes were not changed.

Original installed ordinary staging succeeded with plan
`09e0ed5e284cd3ae9b3f416005b5a4112dd9caa9b59df02ed9e4068f679314bd`
and projection `e6ebdeb0328ff6bd72e702b68e4f989cc193d48c`, bound to source957
and epoch207. Activation committed ownership, then the full-stack readiness check
caught worker-1 exiting1 without OOM. Supported containment and pending recovery
completed. Independent readback at **01:34:22 UTC October7** verifies nine
source/image-bound restart-armed processes, seven healthy endpoints, B0 active207,
ordinary receipts/selector/projection absent, zero native tokens and current write
fences, and the exact plan retired. Do not retry this retired identity.

Captured original worker traceback and an offline replay of its immutable8.9 MB
plan identify a stale Python ownership verifier: it rejects10 Dayforce browser
monitor members and103 detail members across eight deployed profile families.
Candidate958 adds the missing browser and all nine compiled detail variants;
its replay produces **the exact original Go projection SHA1**. The legacy verifier
still enforces source, epoch, plan, worker, canonical config and exclusive detail
boundaries. The native actor's later retained `locations` startup label occurred
during containment and does not establish an independent taxonomy failure.

Candidate **0.13.958** groups this concrete cutover correction with
**Governmentjobs + Zoho Recruit RSS**: four new compiled monitor profiles,
85 installed capabilities. Sixteen actual Python parser cases cover descriptions,
locations, employment type, metadata and source identity. Twenty-four real
PostgreSQL/Redis cases cover three detail assignments, malformed XML,404,
publisher reservation, canonical fields, deadlines and queue settlement. Six
cold-reversal cases preserve canonical state and ownership refusal. Full Nextdata
identity regression and native executable startup pass; Python ownership tests
pass41 with six environment-dependent skips. Full suites, required CI and
installed-image parity remain required before delivery.

The selected standalone renderer was independently verified against its original
release generation, immutable image, network policy, PKI, isolation and actual
ARM64 Lightpanda **1.0.0** binary hash. The official non-prerelease release list
was rechecked October7; [1.0.0](https://github.com/lightpanda-io/browser/releases/tag/1.0.0)
remains the latest stable release.

Source957 verification includes 36 focused canonical detail cases using the
credentialed CONNECT fixture for network requests, PostgreSQL/Redis effects,
content fields, publisher policy, retries, redirects, deadlines and interrupted
claims. Independent ownership and cold reversal passed for all three generic
proxy detail families, including Avature learned-portal reversal. Full worker
and queue race suites passed in 479.394 and 264.916 seconds; installed-image
parity verifies all81 profiles. Refusal cases make no network requests; explicit
sealed-transport cases use mocks.

Original [B0 whole-lane benchmark 37542921649](https://github.com/colophon-group/jobseek/actions/runs/37542921649)
completed all16 synthetic trials with exact canonical output, queue accounting
and cleanup. At concurrency four, synchronized whole-lane peak RAM was
11.65–12.28% of the Python control, lifetime CPU 4.63–4.67s versus
10.36–10.88s, and fixture density 12.86–13.49 times the control. This proves the
bounded B0 fixture; full ordinary production workload cost remains unproven.

Continue in these grouped deliveries:

1. Deliver candidate958 through exact-head required CI and immutable deployment.
   Retire supported B0 ownership before rollout, capture a fresh source/receipt-bound
   cohort, then stage/activate a new plan and independently prove fleet, ownership,
   canonical fields, publisher policy, freshness and queue behavior.
2. Continue grouped DOM rich rows and remaining RSS provider, URL and transport
   variants. Current configuration evidence identifies28 rich-row DOM configurations
   and26 RSS configurations before the two provider additions in958.
3. Port remaining browser actions/capture, API pagination/enrichment and provider
   families in batches selected from fresh canonical configuration census. Preserve
   every enabled board and independent detail configuration.
4. Complete native scheduling, persistence, runtime maintenance and deployment
   consumers; measure comparable whole-lane CPU/RAM/density/attributable cost.
5. Establish replacement authority, supported quiesced cutover/cold reversal and
   rollback window; retire production Python, Playwright, Chromium and legacy
   runtime assets while retaining useful isolated offline Python tooling.

Private raw census, exact image receipts and operational logs are protected host
or local evidence. Keep portable checkpoints free of credentials and raw metadata.
Full migration completion requires all steps above, beyond compiled eligibility.

### Prior releases and evidence

Production **0.13.949** was deployed from
`c7f789faef866c0fd77921750095f1a6253c707d` through original immutable deployment
[37417474358](https://github.com/colophon-group/jobseek/actions/runs/37417474358).
Both ordinary activation attempts were refused before ownership activation.
The second refusal is explained by three UKG detail configuration hashes changing
as their legacy monitors learned `listing_url`, `host`, `tenant` and `board_id`.
The first refusal remains unexplained. Both original pending recoveries succeeded.
Original B0 rollback and selector clear then completed; independent readback at
06:50 UTC verified epoch 200, six healthy legacy services, no native claim tokens,
no ordinary projection or active owner receipt, and the old ordinary plan staged
and inert. Historical immutable audit receipts remain intact.

[PR #10336](https://github.com/colophon-group/jobseek/pull/10336), release
**0.13.950**, ports **Softgarden + UKG + BambooHR + Recruiter.co.kr** together:
55 current monitor configurations and 41 detail bindings. Its exact reviewed head
`ce311df349d75ea799fb2be90248027ca23a19cb` passed all required and installed-image
checks and merged to `e310db696b8815ba11201aad97d57fd69f9d4a3e`. Original full
[deployment 37426031356](https://github.com/colophon-group/jobseek/actions/runs/37426031356)
succeeded. Independent readback at 07:10 UTC verified its exact immutable images,
six healthy legacy services, cold native actors and epoch 200. The account-recovery
fixture targets the actual account action;
early Lightpanda capacity resets retain bounded renderer retry classification.

[PR #10337](https://github.com/colophon-group/jobseek/pull/10337), release
**0.13.951**, ports **DOM + RSS + Inline + API-sniffer variants** together.
Local eligibility covers 62 DOM pagination and 20 DOM rewrite configurations
(overlap), 11 generic RSS feeds, 4 inline cache-bypass routes and 43 explicit API
enrichment assignments. Twenty-five actual Python HTTP comparisons, 12 canonical
PostgreSQL/Redis cases and 12 cold-reversal cases pass. Full queue and worker race
suites pass in 172.126 and 323.882 seconds. All exact-head required checks and
installed-image parity passed; reviewed head
`368ca83e25eaeeb1df7d3338c71efed84b6877e5` merged to
`20e29703d8bf48594e91f85c02d6d075ca963ab7`. Original full
[deployment 37427352256](https://github.com/colophon-group/jobseek/actions/runs/37427352256)
succeeded. Independent readback at 07:32 UTC verified the exact original images,
six healthy legacy services, epoch 200, absent native claims/projection/receipts
and the old 949 plan staged and inert. Lightpanda **1.0.0** remains the latest
stable release in the official release list, rechecked October 6.

Release **0.13.952** ports **Comeet + Jobvite** together:
Comeet hosted assignments and configured public API feeds, and Jobvite static
listings, branded linked listings and category pagination. Local registry proof
covers 9 Comeet and 9 Jobvite monitors, including all 9 Jobvite detail assignments
through existing direct DOM/JSON-LD or rendered JSON-LD routes. Thirty-seven actual
Python HTTP comparisons and canonical PostgreSQL/Redis settlement comparisons
pass. Three publisher reservation cases, including a category resource, preserve
existing postings. Six new cold reversal cases pass. Full queue and worker race
suites pass in 177.608 and 345.917 seconds; final strict-anchor HTTP/canonical
checks pass in 14.313 seconds. API parser race, vet, module tidiness and formatting
checks pass. [PR #10338](https://github.com/colophon-group/jobseek/pull/10338)
passed all exact-head required CI and installed-image parity at
`80807c17947a09ccd9a7c65161f32591588e02d2` and merged to
`bd043e3ba94761d01ace0181d7ddf22e63458e50` after fresh head-bound merge authority.
Original full [deployment 37431155889](https://github.com/colophon-group/jobseek/actions/runs/37431155889)
succeeded through its original build, deploy and promotion. Independent readback
at 07:57 UTC verified its exact source/image identities, all six healthy services,
epoch 200 and absent native authority; the old 949 plan remains staged and inert.
No local parser or registry proof grants production ownership.

The next grouped candidate **0.13.953** ports **Paycom + Rippling**, including
both monitors and all required detail scrapers: 16 Paycom and 10 Rippling current
registry configurations, 26 native detail bindings and four profiles, bringing
the installed total to 63. Paycom refreshes its public bootstrap and paginated
previews, preserves Hybrid detail authority and uses the existing Go salary
parser. Rippling lists canonical public URLs and hydrates its V1 detail records.
Both use the existing sealed HTTP, publisher policy, canonical persistence,
claim settlement and source-bound ownership paths.

Twenty-nine monitor and 27 detail request/output cases come from the actual
Python implementations; Paycom salary references use the independent legacy
Python parser. Twenty-nine monitor and 26 admitted detail canonical PostgreSQL/
Redis cases pass, including exact deadlines and lease conservation. Six publisher
reservation cases cover both provider resources and both Paycom bootstrap phases.
Two Paycom Hybrid refresh cases preserve authoritative detail content; two detail
mask/backfill cases preserve selected field authority. Eighteen new monitor/detail
cold reversal cases pass, covering interrupted and committed claims, configuration
drift, recovery and failed Redis restoration. API race, vet, module tidiness and
Python fixture lint/format checks pass. The full queue race suite passes in
196.065 seconds; the full worker race suite passes in 350.513 seconds.
[PR #10339](https://github.com/colophon-group/jobseek/pull/10339) passed exact-head
Required CI, Crawler Deploy Gate and installed-image parity at
`2f9d2060a378d412d4a08175757509bb2fdfe496`, then merged to
`baf9e70ee800154475d845710c3dd4ae2d8a7229` after fresh head-bound authority.
Original full [deployment 37435753033](https://github.com/colophon-group/jobseek/actions/runs/37435753033)
succeeded. Independent readback at 08:46 UTC verified its exact source and
immutable images, all six healthy service endpoints, epoch 200 and cold native
ownership. Original supported B0 activation succeeded and independent readback verified
its active cdom receipt at epoch 201, seven healthy endpoints and one native
render/commit. Fresh ordinary reconciliation checked 1,435,073 actual scheduled routes and
admitted 7,085 monitors plus 2,541 detail boards covering 1,409,808 postings.
Thirty-eight posting-cache mismatches and one unsupported route retain their
current owner. Original exact-plan staging succeeded, but activation was refused at
`redis-save` at 09:23:48 UTC, immediately as automatic BGSAVE started. The
original pending recovery succeeded; independent 09:26 UTC readback verified
nine restart-armed services, seven healthy endpoints, active B0 epoch 201, absent
ordinary owner/projection/tokens and the ordinary plan staged/inert. The isolated **0.13.954** fix waits only for Redis
explicitly refusing SAVE during BGSAVE, within the existing 120-second budget.
It revalidates the server incarnation before retry and still requires its own
synchronous SAVE acknowledgment before SQL authority. Real private Redis tests
reproduce the refusal on unchanged 953 and pass with the fix, including bounded
cancellation; full queue race (383.506 seconds), focused worker race (23.015 seconds), Required CI, Crawler Deploy Gate and installed-image parity pass.
[PR #10342](https://github.com/colophon-group/jobseek/pull/10342) merged its exact reviewed head `00f44a537b41a2794da31d3b6daa1495b588f7b1` to `96601ce7fcadfa341aa153327918e0c71a84419a`.
Before merge, original supported B0 rollback and selector clear succeeded;
independent 20:28 UTC readback proved healthy exact-953 services, cold native
ownership, empty native projections/tokens and retired epoch 202. Historical
receipts remain at older epochs. Original full deployment
[37526934505](https://github.com/colophon-group/jobseek/actions/runs/37526934505)
succeeded. Independent 20:45 UTC readback verified promoted source
`96601ce7fcadfa341aa153327918e0c71a84419a` and original immutable crawler/browser
image digests, healthy services and cold native ownership at epoch 202.
Original supported 954 B0 selector staging and activation succeeded. Independent
20:50 UTC readback verified the exact revision/images and active cdom receipt at
epoch 203 with seven healthy endpoints. Fresh source954 admission completed:
1,447,321 actual scheduled routes, 7,085 monitor boards, 2,541 detail boards and
1,421,855 owned postings. Thirty-eight cache mismatches and one unsupported route
remain excluded. Original staging succeeded for plan
`8b52154bd1551274123a16b620143b11d55965d0dc082e4cd2f6f2005ff6d2c6`.
The original activation committed SQL/Redis authority at 21:10:48 UTC, including
its synchronous Redis SAVE. The wrapper then failed its container readiness
check and contained the complete lane. Original supported `recover-pending`
succeeded. Independent 21:17 UTC readback proves nine exact-image services running
with restart policies armed, seven healthy endpoints, active B0 at203, a retired
ordinary plan, absent ordinary receipts/selector/projection, no current write
fences or native claim tokens, and no administrative containers.

The initiating service failure remains unproven: the old wrapper reports no
service name, and recovery recreated the containers before their states could
be captured. The retained native worker log reports startup stage `locations`,
which can also result from cancellation during containment. The unchanged source954
location loader passes against a read-only production taxonomy snapshot in an
isolated local PostgreSQL schema (37,526 locations, 143,004 core names; 1.106 seconds).
This local replay does not prove the original Linux process completed startup.
The candidate wrapper now inspects exited containers and reports only its fixed
service name, process state, exit code and OOM flag before containment. No
activation retry or runtime overlay was used to recover production.
The four-provider candidate advances together to **0.13.955** after this fix.
Local proof does not grant production ownership.

The upstream salary DTO is independently compared. Canonical SQL salary comes
only from description text in the actual legacy processor; native execution
preserves that behavior. The reference test was corrected to expect NULL salary
columns for descriptions without compensation text. No runtime behavior changed
to accommodate the mistaken test expectation.

The **0.13.955 ADP + Paylocity + Cornerstone + Dayforce** batch is in progress
in the isolated `fix-crawler/native-api-provider-batch-five` branch. Three HTTP
monitors and ADP/Paylocity details are implemented locally, using the existing
fetch, salary/enum, persistence and ownership paths. ADP handles its bounded DOCX
fallback, title-location pattern and rich pagination; Cornerstone refreshes public
authorization and validates paginated inventory; Paylocity reads embedded records
and its server-rendered detail markup. Seven candidate profiles bring the local
capability list to 71 with Dayforce's compiled browser session profile, including
distinct Paylocity proxy monitor/detail routes. The four-provider scope remains
unchanged and unpublished.

Thirty-nine field comparisons from actual Python pass, including Unicode
location deduplication, custom employment labels, arbitrary-precision IDs and
Lexbor detail markup. Forty-three actual Python HTTP request/output comparisons
and forty-three real PostgreSQL/Redis canonical settlement cases pass in
35.533 seconds, including ADP declared attachment size limits. The actual
Python HTML normalizer independently freezes canonical DOCX descriptions; raw
upstream tables are normalized by the existing processor. Ten DOCX malformed-input references additionally pass, including inert
external DTD declarations and rejection of malformed XML/root bodies. Final
HTTP plus detail canonical checks pass in 9.500 seconds after those corrections.
Twenty-one new cold
reversal cases pass in 38.365 seconds. API full race passes in 5.018 seconds;
vet, module tidiness and Python fixture lint/format pass. Seven new publisher
reservation cases and four description-mask/backfill cases pass in 8.351
seconds, including both Cornerstone resources and ADP attachment responses.

Registry proof currently covers 14 direct ADP, 12 Cornerstone and four direct
Paylocity monitors. Two independent ADP scrapers preserve their API-sniffer
monitors, and five Paylocity details preserve their independently configured
direct transport, including two boards whose monitors use a proxy. This gives
22 direct detail bindings including one existing JSON-LD assignment. Five
Paylocity proxy monitors and three explicit proxy details now have distinct
compiled Go profiles; independent direct scrapers remain direct. The protected
Webshare client ports the process-local endpoint pool and generation-owned
quarantine/recovery policy. All 3,078 operations in 34 actual Python policy
traces match. Real HTTP/CONNECT tests verify redirect exit affinity, cookies,
pinned TLS trust, credential isolation, public-origin validation, stream
outcomes and shared connection bounds. The supported wrapper retains protected
endpoint credentials and excludes the operator API key; all 14 existing driver
tests and one additional credential-scope test pass.

All 43 frozen canonical cases plus 11 Paylocity cases through a real proxy hop
pass PostgreSQL/Redis settlement, description, schedule and lease checks
(51.317 seconds including proxy policy/transport checks). Nine publisher
reservation cases and six enrichment mask/backfill cases pass (5.398 seconds
including startup tests). Nine additional proxy cold reversal cases pass
(8.957 seconds), retaining interrupted/completed attempts and refusing stale
writes. Registry and API full races pass in 2.457 and 6.321 seconds. Proxy
connection bounds pass separately in 3.421 seconds; HTTPS-proxy trust and
authenticated SOCKS5 handshakes pass in 1.771 seconds. Worker/queue vet pass.
These are local execution proofs, not installed or production ownership.
Dayforce's ten browser boards have local Go identity/bootstrap, job projection
and overlap-aware search pagination implementations. Eighty-nine comparisons
from actual Python pass: 51 identity/bootstrap/page cases, 17 rich field cases,
14 pagination cases and seven actual HTTP bootstrap cases; final worker race
passes in 3.338 seconds. Controller-private search capture and compiled browser
POST helpers now correlate main-frame request/response identities, keep CSRF
credentials out of results/logs and erase them on cleanup. Twelve actual Python
CSRF header comparisons and capture/compiler race tests pass (1.329 seconds).
The helpers now connect to one fresh Go Lightpanda target and a compiled Dayforce
conversation on the existing pinned TLS/C4 service. The worker-side method uses
verified HTTP bootstrap, site-identity matching and its existing complete inventory
validator. Fifteen comparisons from actual Python verify search retry and
publisher-policy order, including JSON errors, 401/403 refusal and positive
headers/metadata. The Go/TLS tests prove retained C4 capacity, cleanup before
success, disconnect cancellation and offset refusal before contact. Full pilot
race passes (8.981 seconds); worker Dayforce race passes (1.956 seconds). The renderer
builder now uses the repository's immutable Go 1.26.4 pin and four explicit pure
Go library contexts. Local deployment-contract tests pass (69 passed; 24 environment skips); those skips do not qualify installed Linux behavior.

Dayforce registry admission and browser worker dispatch now use
`dayforce.session-search/v1`; all ten registry boards compile. The original
[dual-architecture pilot run 37538759994](https://github.com/colophon-group/jobseek/actions/runs/37538759994)
at `bb9e4ef2a31033825ceda89904fbe1b48a2612d5` proves actual checksum-pinned
Lightpanda 1.0.0 HTTPS, cookies, CSRF capture and overlap/retry pagination on
amd64 (0.20 seconds) and arm64 (0.17 seconds). Nine real PostgreSQL/Redis cases
through the production client and worker prove canonical descriptions, skip
details, policy reservation, failed-page refusal, site/configuration drift,
cleanup refusal, gone evidence and browser deadline/lease conservation
(3.938 seconds). Four cold reversal cases preserve browser deadlines through
Redis restart and allow later retirement after historical Dayforce receipts
(5.237 seconds).

That initial pilot run is terminal red at its later child-identity probe: the upgraded
Go runtime opens `/sys/fs/cgroup/cpu.max` itself for CPU quota tracking. The
candidate attestation accepts only that exact cgroup-v2 filesystem descriptor
with read-only and close-on-exec flags; arbitrary files, other quota paths and
inheritable descriptors remain refused. The original fixed-image
[run 37539866720](https://github.com/colophon-group/jobseek/actions/runs/37539866720)
is green on both architectures, including child identity and density smoke.
The explicit read-only public qualification in
[run 37540756090](https://github.com/colophon-group/jobseek/actions/runs/37540756090)
at `58304e612b64e5a1e8cc7b248374127ce4085597` passes all ten current Dayforce
registry boards through verified HTTP and actual installed browser searches.
It covers the first two pages where available, including overlap0/5/10; complete
live inventory and production authority still require the ordinary worker.
The first qualification attempt omitted the registry's empty-config default;
its eight configured boards passed and its two blank configurations stopped
before contact. The qualification now applies the normal `{}` default.
Full worker and queue race suites pass in 413.569 and 235.601 seconds,
respectively. Grouped exact-head required checks and deployment remain.
Lightpanda 1.0.0
remains the latest stable official release, rechecked October 7. No Dayforce
production ownership is claimed.
The generic B0/B1 adapter continues to reject captures and origin-contact evaluation;
the provider conversation grants no queue, persistence or generic resumption
authority. A direct stateless Dayforce replay is not a verified replacement.
Complete remaining grouped checks and operational proofs,
then grouped publication. The implementation is saved in
commits on the isolated batch branch; no four-provider PR is published. These
local counts grant no live authority.

Next batches should close shared variant gaps across providers. Candidate955
code screens 7,133 of 7,885 canonical monitor configurations in the retained
source954 snapshot; 752 remain unsupported. This is historical configuration
screening, not fresh cutover admission. DOM (280) and API-sniffer (149) make up
the largest remaining groups. Extend the proven shared HTTP proxy transport
across existing DOM/API, Inline, Sitemap, RSS, Eightfold and Phenom profiles in
one batch, preserving independent detail transports. Then port remaining DOM
actions/rich rows and browser API capture together, using existing parser,
publisher, session and persistence paths. Group the smaller remaining providers
by common HTTP/HTML/session behavior, rather than issuing one release per type.

Finish remaining provider/browser/filter variants and runtime consumers using
the existing delivery paths. After the selected complete immutable deployment,
independently verify its baseline, activate the supported B0 cohort and capture
fresh canonical/cache/actual-route admission before ordinary staging. Never retry
the stale 949 hash or ignore learned UKG drift. Verify active ownership,
every-profile freshness and queue conservation; compare whole-lane CPU/RAM/cost;
prove cold reversal and establish the rollback window. Retire production Python,
Playwright/Chromium and obsolete runtime assets after replacement authority is
verified, preserving every enabled board and useful isolated reference tools.

Protected operational evidence and exact live handles are in the private durable
checkpoint. All sections below this current summary are historical snapshots.

## Historical delivery — 2026-10-06 00:04 UTC

The full Go + Lightpanda migration goal remains active. Release batches group
multiple provider types and use one shared verification/deployment cycle.

**Jobylon + NextData (80 configurations)** merged in [PR #10330](https://github.com/colophon-group/jobseek/pull/10330)
as `9595042b1423f2aca60e2da6f27195c1beb4b828` after Required CI, Crawler Deploy
Gate, runtime contracts and installed-image parity passed at the exact reviewed
head. The original immutable [946 deployment](https://github.com/colophon-group/jobseek/actions/runs/37387692274)
succeeded. Independent 23:42 UTC observation verified all protected release
identities, both actual image digests and six healthy restart-armed baseline
services. Original Lightpanda B0 activation and independent readback succeeded
at epoch 195 with seven healthy endpoints. Fresh reconciliation checked
1,398,049 scheduled posting routes and admitted 6,595 monitors and 2,475 detail
boards covering 1,374,668 postings. Thirty-eight detail boards retain legacy
ownership because their actual posting-cache routes differ. The original
ordinary staging succeeded; its exact-plan activation is running. Ordinary
serving and naturally scheduled canonical processing remain unverified.
[946 production evidence](evidence/go-native-embedded946-production-2026-10-06.json).

**Inline + Beisen** is the next grouped batch. All 24 Beisen canonical/cache
configurations and 100 of 125 Inline configurations admit locally against the
fresh 946 source-bound capture. The remaining 25 Inline configurations still
need browser/proxy/TLS or invalid alternate-header qualification. Publish the
124 verified configurations together after final affected suites and required
exact-head CI. Keep all remaining configurations in the full migration scope;
do not publish a Beisen-only iteration or delay this grouped delivery for every
remaining browser variant. The two remaining NextData routes (Yum China global
evaluation and Revolut stealth) also remain required.

Beisen's modern bootstrap/API inventories and legacy table/inline listings pass
42 actual Python request/output/failure cases and 15 real PostgreSQL/Redis
worker modes. Partial legacy rows preserve fully scraped title, locations,
description and hash. Disabled portals and root/first-listing terminal responses
use the existing spaced disappearance confirmation. API or later-page failures
cannot authorize disappearance or earlier-prefix writes.
[Beisen local evidence](evidence/go-native-beisen-local-2026-10-05.json).

Inline reuses the production DOM tokenizer, step engine and rich writer. Its
native static path now covers defaults, title/description filtering, stable
synthetic/provider IDs, ordered same-origin source URLs, expiry and inclusive UTC
deadlines, explicit-empty witnesses, aggregate-position expansion, ordered
alternate URLs, static JSON wrappers and bounded retries. Twenty-one foundation
cases, 51 complete inventory cases from 46 actual Python tests, 20 transport
cases and 15 real PostgreSQL/Redis modes pass. Public-header routes retain the
installed native same-origin/cookie-free boundary; two frozen legacy forwarding
cases are compared explicitly against that native contract, not claimed as
identical legacy requests. Verified expiry remains a probe witness; runtime
empty results preserve the existing six-check confirmation window. Profile and
cold-retirement proof passes. The final full worker race suite passes in 215.148s with required real PostgreSQL/Redis; the full queue suite passes in 110.596s.
[Inline local evidence](evidence/go-native-inline-local-2026-10-06.json).

Lightpanda 1.0.0 is still the newest stable release in the official release list,
rechecked October 6. Full native ordinary adoption, remaining enabled monitor/
detail/browser and maintenance consumers, canonical/policy/freshness/queue
proof, comparable whole-lane resource/cost measurements, supported cold reversal,
rollback window and production Python/Playwright/Chromium retirement remain
required before goal completion.

## Delivery order from this checkpoint

1. Finish the original 946 activation and source-bound operational proof, or its
   original contained recovery with a diagnosed administrative failure phase.
2. Publish Inline + Beisen together: the verified local target is 124 boards,
   with one combined PR, required-check cycle and immutable release. Retain all
   149 extraction configurations as the full target.
3. Group remaining public inventory variants into subsequent deliveries. The
   fresh 946 monitor census still leaves 73 RSS, 33 sitemap and 10 Personio
   configurations outside the admitted profiles. Qualify their actual enabled
   variants through the existing extraction/transport/lifecycle paths. Larger
   subsequent cohorts include Eightfold (30), Almacareer (23), MokaHR (22),
   UKG/Paycom (16 each) and the remaining API/DOM options. These counts describe
   current legacy ownership, not a promise that every configuration shares one
   replacement profile. Each normal iteration should qualify multiple types.
4. Complete rendered Inline and remaining NextData global/stealth routes with
   qualified Lightpanda or proven public sources, without discarding configured
   actions, proxy or TLS behavior. Keep those variants in the durable backlog.
5. Complete native maintenance/deployment consumers and the full operational
   acceptance: natural posting persistence and freshness, queue conservation,
   comparable resource/cost measurement, cold reversal and rollback window.
   Then remove mandatory production Python/Playwright/Chromium. Useful isolated
   offline Python research and corpus generation can remain.

## Earlier grouped delivery — 2026-10-05 22:47 UTC

The full migration goal remains active. Routine migration releases now group
multiple provider types and share verification, checkpointing and deployment.

The startup/iCIMS prerequisite [PR #10328](https://github.com/colophon-group/jobseek/pull/10328)
merged at the verified head as `194af110324d6eb9a09fc23619f516d656e608b5` after
`Required CI` and `Crawler Deploy Gate` passed. Its original immutable
[deployment](https://github.com/colophon-group/jobseek/actions/runs/37370996768)
completed and promoted on retry attempt 2. Independent readback verifies its
exact image digests and six baseline service health responses. Fresh original
B0 staging/activation reached epoch 193 with seven healthy endpoints. A fresh
reconciliation admitted 6,409 monitor boards and 2,461 detail boards covering
1,375,814 scheduled postings. Ordinary administrative activation rejected before
serving. Original pending recovery succeeded; independent 22:04 UTC readback
verifies the absent ordinary receipt, unchanged B0 receipt/epoch, nine restart-
armed services, all seven health endpoints and 22 ready/0 inflight/0 dead.
Ordinary production processing remains unverified. Post-recovery comparison
finds one embedded detail board with changed tenant/listing configuration; it
does not establish the exact original rejection timing. Before the next release,
original B0 rollback and selector clear succeeded. Independent 22:11 UTC proof
verifies six healthy restart-armed baseline services, epoch 194, no active SQL
plan/current fence, no native queue owner/projection/receipts/selectors and no
administrative oneoff. [Production evidence](evidence/go-native-icims944-production-2026-10-05.json).

The five-provider release [PR #10329](https://github.com/colophon-group/jobseek/pull/10329)
groups Breezy, Gem, JazzHR, Gupy and direct Phenom: 106 enabled configurations.
Only its four own commits were rebased onto the prerequisite merge. Its new
head `c47aee3575e694ac7061b6291e0346061e8c5d11` passed fresh `Required CI`,
`Crawler Deploy Gate` and installed-image parity. A fresh exact-head/base/state/
operator/hold audit bound its merge as `5472e167a06ac543bfa4529e286d60df51d90d05`.
Its original [deployment](https://github.com/colophon-group/jobseek/actions/runs/37380963344)
completed and promoted. Independent 22:32 UTC readback verifies exact source
and image digests, all six healthy restart-armed baseline writers, cold epoch 194,
no native receipts/queue owner/active SQL plan/current fence, cleared selectors
and no administrative oneoff. Native ordinary adoption remains unverified.
Lightpanda 1.0.0 remains installed and the official stable release list was
rechecked on October 5. [Production evidence](evidence/go-native-providers945-production-2026-10-05.json).
Only this batch's own implementation commits were rebased onto the merged release.

[PR #10330](https://github.com/colophon-group/jobseek/pull/10330) groups
**Jobylon + NextData**; 80 of its 82 target configurations are verified locally.
Required CI and Crawler Deploy Gate must pass at the current head before merge.
The full target remains 82 enabled
configurations. Jobylon's 28 canonical/cache monitor and detail configurations
pass local admission; four description masks use the existing JSONLD enrichment
writer. Nineteen actual Python fixtures, 14 real worker modes, description-mask
persistence and three cold-retirement cases pass. Full required local queue and
worker race suites pass in 120.683s and 230.456s; 93 runtime-contract tests pass.
[Jobylon evidence](evidence/go-native-jobylon-local-2026-10-05.json).
The NextData document decoder now reuses the existing precise JSON/RSC/React
Router implementation and preserves the monitor's strict first-script and
Phenom Canvas behavior. Its rich field/template/slug projection also passes seven actual Python cases.
Fourteen document-source cases and the complete API module race suite pass.
The runtime now admits 52 of 54 canonical/cache configurations: 47 direct,
four render-only routes and the exact Florida Courts browser expression. Its
typed Go document transform uses the existing held Lightpanda navigation.
Twelve actual offline Chromium/Python cases and three real PostgreSQL/Redis
worker modes pass, including malformed later-row refusal before writes or
disappearance effects. The captured current upstream document contains two
items; its complete browser expression value equals the Go result. Thirty-seven actual Python streamed cases pass, including ordered ten-page groups,
required-page retries, tenant checks and committed failure prefixes. Fourteen
real PostgreSQL/Redis worker modes pass, as do cold retirement and semantic
configuration/resource binding. Scraped locale bodies remain authoritative;
missing locales receive the legacy monitor fallback. All three provider-identity routes preserve canonical IDs and URL aliases; the
shared hospital tenant uses bounded detail JSONLD employer witnesses. Selected
description+locations enrichment passes the existing canonical writer. Ten
real rendered worker modes and rendered cold retirement pass. Full required
queue/worker race suites for these edits pass in 106.294s/220.211s. The remaining
two browser configurations remain in full migration scope: Yum China global
evaluation and Revolut stealth. Keep their current legacy ownership until their
replacement is qualified; do not weaken admission to reach a count.
No production NextData claim is made. [NextData local evidence](evidence/go-native-nextdata-local-2026-10-05.json).

Publish Jobylon + NextData together with the 80 verified configurations.
Do not delay this grouped delivery for the two remaining browser routes.
The candidate also adds fixed administrative cutover phase diagnostics, so a
rejection distinguishes configuration, database/queue, lease wait, cold SQL scope,
projection, B0 capture and activation/retirement without exposing private errors.
The full required queue/worker race suites pass in 107.420s/212.477s after
these additions, covering successful and rejected executable transitions.
Use those diagnostics on the next immutable release before repeating the
previous generic administrative failure.
Group the 125 Inline configurations and 24 Beisen variants in the following
larger extraction batch. The captured census has 107 Simple and 18 Browser
Inline configurations; all 24 Beisen configurations use Simple ownership.
Treat metadata transport requirements as authoritative even when a cached
worker flag differs, and qualify each variant through the existing paths. Continue exact-source deployment/readback
and original B0/ordinary activation and recovery when GitHub runners complete
the current release. Whole-lane comparison, full reversal, rollback window and
retirement of mandatory production Python/Playwright/Chromium remain required.

## Prior serving checkpoint — 2026-10-05 20:13 UTC

Lightpanda **1.0.0**, released October 2, remains the newest stable release in
[the official release list](https://github.com/lightpanda-io/browser/releases),
reverified on October 5. Its immutable renderer and restart/reboot acceptance
remain verified.

[API detail PR #10327](https://github.com/colophon-group/jobseek/pull/10327)
merged as `09996a260785845f3b94a9fafad213d2b622ea3f`; the original immutable
[943 deployment](https://github.com/colophon-group/jobseek/actions/runs/37351158145)
completed and promoted. Fresh reconciliation checked 1,436,577 scheduled posting
routes and admitted 6,291 monitor boards and 2,461 detail boards covering 1,412,265
scheduled postings. Thirty-seven detail boards retain legacy ownership because
their per-posting cache routes differ. Original B0 activation at epoch 191 recorded five
committed renders before recovery; its counters reset during complete restart.
[Production evidence](evidence/go-native-http-api943-production-2026-10-05.json).

The first ordinary activation refused before publishing ownership. Its supported
recovery succeeded after the shared mutation lock released. Later read-only
comparison found four UKG-derived detail bindings had gained `listing_url`;
that observation cannot establish the exact original refusal timing. Fresh
original staging and activation accepted SQL ownership after natural lease expiry,
but complete-stack readiness failed. All four legacy workers exited with
`OrdinaryOwnershipError`; the native worker separately exited with a generic
startup rejection. The original wrapper contained all writers, and its supported
`recover-pending` restored the complete baseline/B0 stack. Independent 19:03 UTC
readback verifies six baseline health responses at 200, B0 claimant at 204,
unchanged B0 epoch/receipt, 22 ready records, zero inflight/dead and absent ordinary
receipt. Native ordinary production processing remains unverified.
Before the incoming944release, the original B0 rollback restored all22ready
records and retired epoch192. Original selector clear succeeded. Independent
20:13 UTC readback verifies six healthy restart-armed baseline services, no
active SQL plan or current fence, no native queue owners/receipts/selectors,
and no live administrative oneoff.
[Cold baseline evidence](evidence/go-native-http-api943-before-icims944-baseline-2026-10-05.json).
[Activation and recovery evidence](evidence/go-native-http-api943-activation-recovery-2026-10-05.json).

The 944 candidate fixes the legacy validator's missing Oracle API, embedded
direct/rendered and HTTP API detail profiles while retaining worker/domain/company/
config/hash guards. The actual rejected943 plan now produces the exact Go routing
projection for all 6,291 monitors and 2,461 details. Go startup reports only a
fixed stage label, preserving rejection and private error redaction. This makes
its separate unresolved failure observable on the next immutable release.

[iCIMS PR #10328](https://github.com/colophon-group/jobseek/pull/10328) adds URL
inventories through existing Go transport, canonical/detail scheduling and cold
retirement. All 118 enabled canonical/cache configurations are admitted. Sixteen
actual Python fixtures, 21 operational cases and full queue/worker race suites
pass. The legacy compatibility fix passes real PostgreSQL/Redis and executable
startup regressions. Fresh required checks must cover the updated head before
merge. [Local evidence](evidence/go-native-icims-local-2026-10-05.json).
Production iCIMS ownership remains unverified.

Use original B0 rollback and selector clear, verify the baseline, then merge and
deploy944 after current required checks and exact-head authority. Repeat fresh
source-bound admission and the original full cutover, inspect any static startup
failure, and prove natural Simple/rendered canonical output, publisher policy,
description publication, freshness and queue conservation. Continue remaining
enabled profiles and mandatory Python consumers through existing paths. Comparable
whole-lane resources, cold reversal and the rollback window precede final runtime
retirement. Full migration remains active and incomplete; following entries are
historical.


The pending 945 batch adds **106 direct configurations** through the existing
native queue, sealed transport and canonical writer: 13 Breezy, 13 Gem,
20 JazzHR, 36 Gupy and 24 Phenom. Canonical and cached configuration bindings
match for this candidate cohort. Gem uses the existing shared enrichment path;
the other inventories schedule their existing detail contracts separately.
Configuration admission does not establish production ownership or live upstream
processing.

The actual Python comparison corpus contains 45 request/inventory cases. Real
PostgreSQL/Redis checks cover 55 worker cases and 15 cold-reversal cases, including
publisher policy, complete and incomplete inventories, canonical writes, queue
conservation, interrupted and committed-before-ACK retirement, and changed-binding
refusal. The earlier 82-board batch's final full queue/worker race suites passed
106.642s/214.993s. The expanded cohort's final full queue/worker race suites pass
109.597s/212.601s. Focused Phenom/existing-sitemap worker checks, cold retirement,
the sitemap module, vet/tidy, Ruff and 93 Python runtime contracts also pass.

Phenom preserves locale filtering, single-language shards, selected-child failure,
UTM normalization and publisher resource attribution. All 31 enabled boards supply
a persisted sitemap root; 24 direct configurations are admitted. Seven proxy
configurations remain with their current owner until native proxy parity is proven.
The existing task deadline and body budgets apply, and an incomplete bounded union
cannot authorize disappearance effects.

Local evidence: [Breezy/Gem](evidence/go-native-breezy-gem-local-2026-10-05.json),
[JazzHR](evidence/go-native-jazzhr-local-2026-10-05.json),
[Gupy](evidence/go-native-gupy-local-2026-10-05.json). [Phenom](evidence/go-native-phenom-local-2026-10-05.json). Publish the verified
combined work as one 945 release, including the startup/iCIMS prerequisite if it
has not yet merged. Rebase only the batch's own commits if 944 merges first.

The 944 PR remains ready at `b94d8a1d6531192085d0c0744e26949bab3be5c2`.
Native execution and installed-image checks passed. Python typing passed on retry;
the company-reference job again failed to acquire a GitHub hosted runner. Its
unchanged-source fourth attempt is pending. Required CI and Crawler Deploy Gate
must both pass before a fresh exact-head merge. The verified serving baseline
remains healthy at epoch 192; no native owner or selector is active.


## Direct HTTP API details — 2026-10-05

Lightpanda 1.0.0 remains the verified latest stable renderer. The original
v0.13.941 deployment completed. Independent readback at 17:21:59 UTC verifies
source `e8576cb5653173110983fe38a2c79dfe44018f26`, the workflow-published slim and
browser images, six healthy restart-armed baseline writers, cleared native
selectors/receipts, retired ordinary ownership and epoch 190. An earlier readback
refused while an administrative oneoff was running; the successful repeat found
none. [Production evidence](evidence/go-native-coverage941-production-2026-10-05.json).

The selector correction in [PR #10326](https://github.com/colophon-group/jobseek/pull/10326)
merged as `9591fcb38e8c18095c01974f92181da78e0dfd9b` after fresh exact-head, base,
operator, deployment-hold and required check verification. Its original immutable
deployment is [run 37347216841](https://github.com/colophon-group/jobseek/actions/runs/37347216841).
Do not activate a large ordinary cohort on 941 before 942 is verified promoted.

The v0.13.943 batch adds configured `api_sniffer.http-detail/v1` to the existing
worker, HTTP transport, field extractor, selected enrichment and canonical writer.
The captured production configuration census has 41 direct HTTP API detail boards
and all 41 pass configuration admission. This count is not actual posting-route
or live provider verification. Named Python regex groups and escaped fallback IDs
bind GET/POST endpoints and raw bodies; optional authentication responses supply
validated scalar headers. Requests retain the shared 30-second bound, three
existing retries, per-claim cookies and redirects. Successful publisher opt-out
headers stop before JSON extraction, including the authentication response.

Frozen Python output and request fixtures verify URL/query/fragment binding,
19-digit identifiers, header scalars, selected JSON and structured extras. Real
PostgreSQL/Redis tests verify full and selected canonical writes, description
staging, recurring deadline equality, publisher policy, empty/gone/nonretryable/
retryable/malformed failures, wildcard ownership and supported cold retirement.
[Local evidence](evidence/go-native-http-api-detail-local-2026-10-05.json).

Continue by verifying 942's exact promotion, then complete fresh source-bound
B0/ordinary admission and natural Simple/rendered canonical processing. Publish
and merge 943 after green required checks; then finish remaining enabled providers,
configuration options and production Python maintenance/deployment consumers.
Whole-lane resource comparison, supported reversal and the rollback window remain
requirements before Python/Playwright/Chromium retirement. Full migration is
active and incomplete; earlier sections are historical.


## First-time HTTP selection correction — 2026-10-05

Lightpanda 1.0.0 remains the verified deployed renderer. Source940 ordinary
ownership at epoch189 produced 34 rendered monitor completions and eight detail
completions in a bounded observation; no Simple profile completion was observed.
Read-only Redis observation found 15,630 due first-time details across eight
Simple domains, ahead of recurring work. The initial eight-board canonical scan
can miss these priority tasks in a large wildcard cohort. A real PostgreSQL/Redis
regression reproduces that delay with a due ninth board and no claim/error.

The v0.13.942 correction reads bounded first-time candidates from existing ready
queues before rotating canonical traversal. It preserves complete canonical
validation and the original Lua ownership, priority, throttle and lease checks,
and reserves three quarters of the candidate batch for existing traversal.
The new regression and existing rotation, forged-route and fairness tests pass.
[Evidence](evidence/go-native-first-time-selection-2026-10-05.json).

The original source940 retirement initially refused after Redis projection
removal, retaining its retiring identity and containing all writers. Its supported
exact retry succeeded. Independent readback verifies all nine B0/baseline writers
healthy and restart-armed with zero restarts, the SQL plan retired and current
ordinary fences/receipt/projection absent. Original B0 reversal and selector clearing succeeded. Independent readback at
16:52 UTC verifies the complete six-writer baseline at epoch 190, absent native
receipts/projections and the retired SQL plan. PR #10325 merged as `e8576cb565…`
after fresh exact-head required CI/gate and operator/hold checks. Provider
coverage was not yet verified deployed at that checkpoint. Full queue race passed in 92.052s and
worker race passed in 150.785s, and Go vet passed. Production proof
of the selector correction and full migration completion remain outstanding.

## Historical delivery checkpoint — 2026-10-05 16:08 UTC

Lightpanda 1.0.0 remains the latest stable release and is deployed with restart
and reboot acceptance. Source940 B0 is active at epoch189. Two independent
readbacks verify all nine services healthy with exact images, the original
restart policies and zero restarts. Natural render/commit counters rose from
one to three without failures. Scheduled reconciliation completed under the
shared mutation lock; no routine was interrupted or bypassed.

Fresh source940 admission reconciled 1,106,690 scheduled posting routes and
admitted 6,159 monitors plus 2,199 detail boards (1,084,619 scheduled postings).
The three verified B0 boards are excluded. Thirty-seven boards with cache
mismatches retain their existing owner. Original ordinary staging succeeded for plan `76ae97463dc8…` and projection
`8ec189ab057f…`; the original activation wrapper is in progress. Sustained
complete-fleet canonical, publisher, description, freshness and queue proof
remain pending. [Production and admission evidence](evidence/go-native-rendered940-b0-and-admission-2026-10-05.json).

[PR #10325](https://github.com/colophon-group/jobseek/pull/10325) contains the
Oracle HCM and direct/rendered embedded/Next.js coverage batch below. Local
reference, real PostgreSQL/Redis and cold-retirement checks passed. CI built the
new image and exposed a stale 24-profile assertion; the assertion now includes
all 28 compiled profiles. Fresh required and installed-image checks must pass
before a bound merge and original immutable deployment. This batch is not deployed.

Continue remaining enabled providers/options and mandatory production consumers
in the existing worker/parser/writer paths. Prove whole-lane resource comparison,
supported cold reversal and the rollback window before retiring production
Python/Playwright/Chromium. Full migration is active and incomplete. Earlier
sections are historical.

## Historical delivery checkpoint — 2026-10-05 15:42 UTC

The original v0.13.940 release and promotion succeeded. Independent readback
verified source `450b9e0316b813518d8f568afcdd35c4f4312feb`, both workflow-published
image digests, six healthy restart-armed baseline services, cleared native
receipts/selectors, retired ordinary ownership and epoch188.
[Portable production evidence](evidence/go-native-capacity940-production-baseline-2026-10-05.json).
Supported B0 selector staging succeeded; original wrapper activation is in
progress. Fresh B0/ordinary authority and sustained natural processing remain
separate requirements and are not yet verified.

The v0.13.941 local coverage batch now includes configured direct and Lightpanda
embedded/Next.js details in addition to Oracle HCM. It reuses the existing JSON
field extractor, verified document transport, browser navigation, processor and
canonical/queue writer. The 357 embedded/Next.js configurations comprise 353
new direct profiles, one new browser profile and three existing Join profiles;
this count is configuration evidence, not actual posting-route admission.
Structured extras now enrich native details before missing-field defaults, and
embedded HTTP failure preserves the original recurring empty-content policy.

A frozen Python extraction corpus, real PostgreSQL/Redis canonical/publisher/
deadline regressions, direct and browser cold retirement, full affected race
suites and vet passed. See [local coverage evidence](evidence/go-native-embedded-detail-local-2026-10-05.json).
The coverage batch is not deployed. Finish fresh installed admission and natural
proof, remaining providers/configurations and mandatory consumers, then the
whole production resource/reversal/rollback window before retiring Python.
Full migration remains active and incomplete. Earlier sections are historical.

## Historical delivery checkpoint — 2026-10-05 15:17 UTC

Supported source939 retirement and B0 reversal completed. Fresh independent
readback verifies all six baseline services healthy and restart-armed, native
receipts/selectors cleared, the ordinary plan retired, epoch188, and no current
native owner. One interrupted epoch187 receipt remains as revoked history.
[Recovery evidence](evidence/go-native-rendered-baseline-recovered-2026-10-05.json)
records the original successful restoration; final premerge verification repeated
those checks after reconciliation finished naturally.

[PR #10324](https://github.com/colophon-group/jobseek/pull/10324) merged the
reservation capacity-close repair as `450b9e0316b813518d8f568afcdd35c4f4312feb`
after exact-head Required CI, Crawler Deploy Gate and installed contracts passed.
The [original immutable release](https://github.com/colophon-group/jobseek/actions/runs/37331617474)
is in progress; production deployment and fresh native admission are not yet
verified. Lightpanda 1.0.0 remains verified in production.

The Oracle coverage batch below is rebased onto that repair and reserves the
following crawler version 0.13.941. It is not merged or deployed. Next finish
remaining configuration/provider and mandatory-consumer coverage in the existing
worker, validate fresh actual posting routes, and prove sustained natural output,
policy, descriptions and recurring queues before any production Python removal.
Full migration remains active and incomplete. Earlier sections are historical.

## Source939 activation and capacity-reset recovery — 2026-10-05

Lightpanda **1.0.0 is deployed**, with exact image/executable identities and
restart/reboot acceptance verified. [PR #10322](https://github.com/colophon-group/jobseek/pull/10322)
and [production evidence](evidence/lightpanda-1.0.0-production-2026-10-05.json)
record that completed upgrade. The earlier sections below are historical.

Fresh source939 admission reconciled 1,088,087 scheduled postings and admitted
6,159 monitors and 2,199 details. The original wrapper's exact-identity retry
activated one SQL ownership plan and its matching Redis projection at epoch187.
Independent readbacks at 14:12 and 14:14 UTC verified all ten exact-image writers
running with restart policies armed.

Natural validation then exposed an actual runtime failure: the B0 claimant had
12 restarts by 14:16 UTC, starting with a renderer TLS connection reset. Subsequent
strict C4 startup attempts encounter EOF while ordinary work holds renderer
slots. Ordinary Go has zero restarts, but full native natural processing is
**not verified**. Supported full ordinary retirement began at 14:22 UTC; baseline
restoration is still pending. No queue, lease, fence, receipt or lock was manually
cleared. [Bounded recovery evidence](evidence/go-native-rendered-capacity-recovery-2026-10-05.json).

The v0.13.940 repair classifies peer closes only during reservation TLS handshake:
EOF, TCP reset and broken pipe can all result from the service closing an excess
connection before TLS. Both consumers use that classification to wait within
existing cancellation/heartbeat bounds. Initial all-four-slot admission remains
strict; dial, certificate, identity, hello and execution failures retain their
failure behavior. Real TCP peer-close, cancellation-before-claim, resumed
reservation and strict startup regressions gate this release.

Continue with supported full recovery, required CI and immutable release,
fresh bound admission and sustained natural canonical/policy/description/queue
proof. Then finish the existing provider/detail/runtime batch and the full lane's
cost and reversal proof. Full migration is not complete.

## Oracle HCM coverage batch — 2026-10-05

The local coverage branch now wires bounded Oracle inventory and API detail
extraction through the existing native worker, canonical writer, enrichment
processor and Redis detail queues. Description-only enrichment preserves monitor
fields; configured description/employment enrichment and missing-field backfill
use the existing processor. Configured Oracle detail fields reuse the established
API field extractor. The enabled Oracle careers URL allowlist/rewrite preserves
numeric job identity and refuses provider-boundary violations before publication.
Unsupported proxy configurations retain the legacy owner.

Both affected modules pass their full race suites and vet. Real PostgreSQL/Redis
checks cover seven detail outcomes, rich inventory/detail scheduling and cold
Oracle/Workday retirement. This is local implementation evidence, not production
admission or full migration completion. See the
[coverage evidence](evidence/go-native-oracle-coverage-local-2026-10-05.json).
This batch is rebased onto the capacity repair with VERSION 0.13.941. Finish
remaining provider/configuration and mandatory-consumer coverage before cutover.

## Lightpanda 1.0.0 deployed — 2026-10-05

The latest stable upstream release is 1.0.0, published October 2. [PR #10322](https://github.com/colophon-group/jobseek/pull/10322)
merged as `87ceae8dadae7177b5b282bc960b358c706144a7`. Required CI, Crawler
Deploy Gate, real AMD64/ARM64 browser/C4/child/egress checks, renderer deployment
smoke and all sixteen synthetic whole-lane comparison arms passed. That comparison
admitted the candidate; it does not prove complete production migration costs.

The original immutable renderer deployment first refused the old running
predecessor's cgroup memory attestation and contained that exact predecessor
cold. Its supported cold retry passed every unchanged check. Restart/reboot
acceptance subsequently passed; the original deployment resumed the same image.
Independent readback at 13:30 UTC verified the live binary against the official
1.0.0 ARM64 checksum, exact source/image, private cgroup namespace, the existing
1 GiB memory limit and swap disabled. No limits or policy checks were relaxed.
[Portable production evidence](evidence/lightpanda-1.0.0-production-2026-10-05.json)
records immutable identities and original deployment/acceptance runs.

The shared-capacity fix from [PR #10321](https://github.com/colophon-group/jobseek/pull/10321)
is deployed as crawler v0.13.939, source
`d6bdab010404b9bac5e33965d13aec2fc49791df`. Independent readback at 13:09 UTC
verified all six baseline writers healthy, receipts and selectors cleared,
the failed native plan retired and epoch186. Next use fresh source939 native
B0/ordinary admission and prove natural canonical API output and recurring queue
settlement with the upgraded renderer. Then finish the batched provider/config
coverage and mandatory Python consumers, full production comparison/reversal
and rollback window before retiring production Python, Playwright and Chromium.
Full migration remains active and incomplete. Oracle HCM inventory/default detail
extraction is implemented locally with race/vet and pagination/partition tests;
its runtime/profile wiring and production ownership are still pending.

## Full migration delivery — source938 deployed, 2026-10-05

The active goal is full production delivery of Go and self-hosted Lightpanda
across every enabled board, followed by retiring mandatory production Python,
Playwright and Chromium after replacement coverage and the rollback window.
Useful isolated offline Python remains. Full migration is not complete.

[PR #10320](https://github.com/colophon-group/jobseek/pull/10320) merged
v0.13.938 as `6c0793486d8dd18385c692782606eec1b701a11c`. The reviewed and
merged trees agree; Required CI, Crawler Deploy Gate and installed runtime
contracts passed. The [original immutable deployment](https://github.com/colophon-group/jobseek/actions/runs/37298485109)
completed on its second attempt. Independent readback at 11:17 UTC verified
all six exact-image writers healthy and restart-armed before B0 activation.
B0 subsequently activated through its original wrapper at epoch185.

Source937 recovery used the approved938 immutable administrator through the
original installed `recover-pending` wrapper. One interrupted Redis publication
required an idempotent retry; the retry restored all nine services and removed
the ordinary receipt. Independent readback verified the retired SQL plan and
the previously unsettled JSON-LD detail's exact canonical/receipt/Redis future
deadline, with its strike, lease and token absent. Original B0 rollback and
selector clear then restored the full six-writer baseline at epoch184. Twelve
outgoing interrupted receipts remain as history; their retired plan and older
epoch revoke authority. No manual queue, lease, fence, receipt or lock clearing
was used.

Fresh source/receipt-bound admission reconciled 1,085,423 scheduled postings.
It admitted 6,159 monitor boards, including 134 configured HTTP API boards,
and 2,199 detail boards. All three B0 boards were excluded; 37 boards with cached
detail route mismatches were rejected. The first activation published the exact
Redis projection but left SQL staged; the supported exact-identity retry activated
SQL at epoch185. Full-stack startup then refused because the B0 claimant received
TLS EOF while reserving all four renderer slots. A second exact-identity startup
retry reproduced the refusal. The original wrapper retained its identity and
contained every writer. Original `recover-pending` succeeded at 12:08 UTC after
four legacy leases expired naturally. Full B0 reversal and independent baseline
verification precede the next immutable release. Serving ordinary authority and
natural API output remain unverified.

A v0.13.939 continuation fixes the shared renderer startup order: the ordinary
consumer waits for healthy B0 admission before it starts. B0 workers wait before
queue claims when the four-slot renderer closes excess connections during TLS.
Ordinary monitors and details also wait within their existing claim context,
with heartbeat and cancellation still in charge. Shutdown remains cancellable; identity/protocol failures and incomplete initial
C4 admission still refuse. Full supervisor race tests, vet, the scoped deployment
contract and all 14 ordinary cutover tests passed locally. Production validation
is pending required CI and the supported release workflow.

Continue in this order, using the existing worker, queue and writer:

1. Finish original activation, verify all ten exact-image writers and health,
   then observe natural API canonical fields, description staging, deadlines,
   receipts and recurring queue conservation.
2. Batch remaining configured DOM/API/RSS/sitemap/Nextdata options and provider
   families together to reduce repeated cutovers. The current census's largest
   gaps are DOM (361), API (192), Oracle HCM (134), inline (125) and iCIMS (118) monitor boards.
   Implement the long tail and detail coverage; preserve each enabled board's
   owner until its replacement is verified. Do not add infrastructure merely
   to produce another checkpoint.
3. Finish mandatory Python runtime consumers, including legacy worker startup,
   rollback and deployment/maintenance validation. Forward schema setup and
   registry sync, exporter and R2 drain already use Go.
4. Prove the full lane's canonical behavior, comparable CPU/RAM/density/cost
   and supported cold reversal. After the rollback window, remove mandatory
   production Python, Playwright/Chromium and unused runtime assets.

The ARM64 synthetic B0 comparison passed on the same reviewed tree. Its scope
does not establish complete production lane costs. Use focused regressions and
required checks; broaden testing when failures or unresolved contracts justify
it. [Current delivery evidence](evidence/go-native-api-delivery-2026-10-05.json)
records the exact identities and remaining work.

## Configured API migration and contained cold reversal — 2026-10-05

Source937 is currently contained after the original ordinary retirement and
`recover-pending` both refused. All ten writers are stopped and restart-disabled;
there are no live SQL leases or administrative containers. Its original SQL
plan/projection remain active and its wrapper receipt remains `retiring`.
Restoring processing through the supported recovery path takes priority over
promotion. No queue, lease, fence, receipt or configuration was manually cleared.

The bounded configuration audit found all 6,025 monitor bindings unchanged and
one changed independent DOM detail binding: the legacy Avature monitor for
`unifi-uk` learned `portal_id="23"` after admission. Its canonical/cache configs
agree; removing only that newly discovered monitor field reproduces the original
detail hash. The read-only Lua retirement preflight accepts all 6,027 observed
restoration rows. PR #10320 now includes a narrow retirement compatibility case
for that Avature field addition. It requires the original DOM profile, company,
domain, unchanged remaining hash and current canonical/cache agreement. Runtime
claims still reject the changed hash. Changed parsers/listings, configured portal
changes, malformed portal IDs and cache-only changes remain refusals.

The original code reproduces the real queue regression; the corrected six-case
race test passes. Full queue/worker and installed compatibility verification
must pass on the amended head before merge. Use its immutable administrator
through the original installed937 `recover-pending` wrapper; then verify the
restored nine writers, original B0 reversal/selector clear and full six-writer
baseline before promoting938. Do not replace the installed wrapper or restart
individual writers.

The full migration goal remains active. [PR #10320](https://github.com/colophon-group/jobseek/pull/10320) adds
`api_sniffer.http-items/v1` to the existing ordinary Go worker and rich writer.
It ports declared JSON field extraction, GET/POST requests, headers, operation
cookies, page/offset/cumulative pagination and bounded size probes. Structured
responsibilities, qualifications and skills enter the existing enrichment
pipeline before language detection, derived fields and description staging.
Later transient/malformed pages provide no successful inventory. Publisher
reservations bind the actual later page or probe, and total gaps/item caps
suppress disappearance. URL identity preserves Python's raw Unicode behavior.

Local frozen Python oracles cover 46 field cases, 20 discovery cases (one
unported auto-field case remains excluded), 30 rich writer cases and three
exact JSON body byte cases. Nine real PostgreSQL/Redis cases pass under the
race detector, including declared GET/POST pagination, canonical fields,
staged description hash/upload state, failure/policy handling and recurring
queue conservation. Full worker and queue race/vet/tidy checks pass (170.755s and 79.495s),
along with the installed executable cutover/recovery checks, API module and
executor checks. Required CI and installed image parity passed on head `e488021d`.
The exact installed manifest now includes all 24 executable profiles. Fresh CI
is required again for the Avature retirement compatibility amendment.

A read-only census against the source937 configuration snapshot identifies
134 eligible API boards with matching canonical/cache bindings out of 326.
This is a candidate estimate, not fresh production admission. Unsupported
browser/auto-field/filter/rotation/decryption/enrichment configurations retain
their current owner while their replacements are implemented. Before the cold
transition, the 09:19 UTC source937 readback verified all ten exact
images and eight health endpoints, with 24 sampled detail outputs and no
processing diagnostic in the bounded log observation. A strict queue check
found 26 of 27 samples settled; one completed JSON-LD detail retains a Redis
retry strike/earlier queue deadline, while its active posting, canonical future
deadline and receipt agree and no live lease/token exists. Full current
settlement is not claimed. Preserve this evidence through supported retirement
and verify restored canonical scheduling; do not clear the queue manually.

[Candidate evidence](evidence/go-native-api-candidate-2026-10-05.json) records
scope and the remaining delivery steps. Finish runtime and image verification,
required CI and exact-head review, then use original cold ordinary retirement,
B0 reversal and selector clear before immutable promotion. Recreate fresh
source-bound admission, activate through the original wrapper and verify
natural work/queue settlement. Continue remaining enabled profiles and Python
runtime consumers, whole-lane measurements, supported reversal and the rollback
window before retiring legacy production assets.

## Production fleet adoption verified — 2026-10-05

The goal remains full production migration to Go and self-hosted Lightpanda,
including every enabled board, canonical behavior, runtime maintenance and
deployment consumers. Retire mandatory production Python, Playwright and
Chromium after replacement coverage and the rollback window are established;
preserve useful isolated offline Python tools. Full migration is not complete.

[PR #10317](https://github.com/colophon-group/jobseek/pull/10317) merged
v0.13.937 as `a5fd35cfb924800a5ac3fedfedbaca0a457a934c`. Its reviewed tree
matches the merged tree. Required checks passed, and the
[original immutable deployment](https://github.com/colophon-group/jobseek/actions/runs/37272609231)
completed and promoted this source. First, the reviewed v0.13.936 administrator recovered
the contained source934 through the original installed wrapper. Original B0
rollback and selector clear then restored its six-writer baseline. No manual
queue, lease, fence or lock clearing was used.

The selected v0.13.937 six-writer baseline was independently verified before original
B0 activation. B0 now owns `cdom` at epoch 183. Fresh source/receipt-bound admission
excluded all three B0 boards, reconciled 1,084,238 scheduled posting routes and
excluded 37 boards with cached route mismatches. The original ordinary wrapper
staged and activated 6,025 monitor boards and 2,199 detail boards, including 143
rendered DOM monitors, 90 rendered DOM detail boards and 57 rendered JSON-LD
detail boards. The admitted detail boards contain 1,062,208 scheduled postings;
this is the admitted population, not a count of every enabled posting.

Independent readback at 07:18:16 UTC proved all ten selected services running,
restart-armed and using exact expected images, one active SQL ownership plan,
the matching Redis projection, unchanged B0 receipt/epoch and no administrative
containers. Initial natural work committed monitor rows and both static JSON-LD
and rendered DOM details. Sampled detail deadlines match completed receipts and
remain in the future; descriptions reached R2. No processing diagnostic appeared
in this bounded observation. At 07:28:17 UTC, the strict natural settlement
check passed for 17 samples across ten monitor/detail profiles: canonical,
receipt and Redis queue deadlines match, and inflight entries, tokens and retry
strikes are cleared. One initially pending claim settled naturally before the
strict check passed. The claim-error counter remained at one across both natural
observations; this is not a zero-error or full-profile-coverage assertion.

The ordinary plan is
`cb6338813410a3faaeebda772038d885899194a9e8ee3c8fc8e2850d3c838dc3`,
and its Redis projection SHA1 is `a79e0a5fee3d1a78dc0d333490ef27a443c1bf6a`.
The B0 receipt SHA256 is
`4c559854aea8273afdf063ee63c96293b52e9378af61b036b47803dd34ba4040`.
[Portable evidence](evidence/go-native-fleet-adoption-2026-10-05.json) records
the exact source, images, admission and bounded verification.

Continue substantive native coverage batches using the existing workers,
queues, canonical writers and renderer: configured HTTP API/JSON extraction;
remaining DOM/RSS/provider options and families; mandatory Python runtime
maintenance and deployment consumers. Keep this owner selected while observing
natural queue settlement, freshness and whole-lane resources. Before another
runtime promotion, use original cold ordinary retirement, original B0 reversal
and selector clear, then independently verify the complete baseline. Finish
comparable CPU/RAM/density/cost measurements, supported reversal and the rollback
window before removing legacy production runtime assets.

## Expired legacy claims during fleet adoption — 2026-10-05

The delivery goal is the complete Go/Lightpanda migration, preserving every
enabled board and canonical behavior, then retiring mandatory production Python,
Playwright and Chromium after the rollback window. Production recovery remains
the first step; the source934 lane is still contained at this checkpoint.

v0.13.937 reuses cold reversal's canonical deadline restoration during first
adoption. After the original complete-fleet stop and live exclusive SQL barriers
prove no live SQL lease or current native attempt, Redis's clock must also prove
each owned legacy claim expired. The whole cohort is preflighted before queue
restoration or exact ownership publication. Simple and Browser monitors/details
retain canonical deadlines, retry strikes, foreign queues and B0 state; the
operation changes no canonical lease, content or attempt receipt. Live Redis/SQL
leases, wrong namespaces, invalid tokens and corrupt queue types still refuse.
Redis SAVE acknowledgement remains required before SQL activation, and an exact
retry after a failed SAVE retains staged SQL history until persistence succeeds.

The full real PostgreSQL/Redis queue race suite passed in 68.006 seconds. Real
first-owner executable tests passed in 14.104 seconds, including natural SQL
lease expiry, tokenless legacy recovery, staged cancellation and compatibility
retirement. The full worker race suite passed in 110.166 seconds.
These are local candidate proofs, not deployed ownership. The separate
v0.13.936 recovery [PR #10314](https://github.com/colophon-group/jobseek/pull/10314)
passed required CI and merged as `b7ef62601adbd2b33f38d4b5276f78ada1e52c8f`;
its reviewed and merged trees match. Its original immutable build and recovery
of the contained source remain required before promotion. No new recovery
service, host wrapper or queue-clear operation is introduced.

## Configured rendered DOM monitor continuation — 2026-10-05

The full migration remains open. Source934's full eligible fleet staged through
its original wrapper, but activation refused two B0 monitor overlaps. Its pending
identity and contained lane remain retained. [PR #10309](https://github.com/colophon-group/jobseek/pull/10309)
now includes safe cancellation of the never-active staged plan. Required checks
must pass on its new head before the immutable compatibility administrator can
recover the old source through the original wrapper. Correct admission excludes
all three verified B0 boards; no ordinary production adoption is claimed.

The approved935 compatibility administrator passed source verification, but
original pending recovery also refused 18 legacy monitor inflight entries. Read-only
canonical checks proved zero live SQL leases/current native fences and an inert
staged plan; the unchanged B0 audit passed with zero inflight and no ordinary
projection existed. v0.13.936 adds a separate staged-only cancellation operation:
it preserves legacy inflight entries/tokens for the restored workers, removes only
an exact interrupted projection, and keeps full B0 audit, cold SQL/source/epoch
barriers and staged history. Activation/active retirement retain their lease checks.
No current recovery or serving-stack success is claimed.

v0.13.936 extends the same worker, renderer and canonical URL-only inventory
writer to configured single-page rendered DOM monitors. Browser anchor selection,
actual redirected document URL, first document base and WHATWG URL serialization
preserve posting URL identity. It retains the full original configuration binding,
Browser lease/queue namespace, publisher reservation before status, normal
inventory disappearance rules, separate urgent detail routing and canonical
receipt settlement. Renderer/protocol/parser failures remain distinct from
actual critical origin responses for shared host circuits.

Cold reversal restores interrupted Browser monitors from canonical deadlines
and rebuilds ready-domain tiers in the same Browser namespace.
Committed lease expiry restores the successful future deadline and learned host,
and retained old Browser monitor attempts permit later supported reversal.
Separate monitor scan cursors prevent alternating Simple/Browser claims from
skipping one namespace. Exact Lua copies share Browser settlement and recovery.

Real PostgreSQL/Redis queue and worker race suites passed in 51.666s and 108.848s;
subsequent Browser routing/origin503 tests passed in 3.006s. All 38 required Python
ownership/cutover tests and exporter reaper race tests passed. After integration
with amended935, staged cancellation/cutover, rendered queue reversal and
rendered worker compatibility race checks passed in 18.861s, 6.781s and 8.316s. Canonical-only
screening of the source934 census admits 144 rendered monitor configurations;
this is coverage planning, not production authority. Pagination/actions/proxy,
rich rows and provider captures still need native implementation. [Candidate
evidence](evidence/go-native-rendered-monitors-candidate-2026-10-05.json) records
these limits. Production recovery comes first, then fresh exact-source admission,
complete deployment/cutover, natural canonical effects and supported reversal.
Remaining enabled families, maintenance/deployment consumers, resource proof and
production Python/Playwright/Chromium retirement remain required for completion.

## Broader native rendered details — 2026-10-05

The full migration goal remains active. [PR #10308](https://github.com/colophon-group/jobseek/pull/10308)
merged v0.13.934 as `ad31dacbcfe9c132cd75d5e46fbcc3d3b3e3a5d0`; reviewed
and merged trees match. Required CI, installed-image parity and Crawler Deploy
Gate passed. Before merging, the original source933 B0 rollback and selector
clear completed, and independent readback proved all six exact-image baseline
writers healthy/restart-armed at epoch180, no owner receipts/projections,
no current active write fences and no live administrative containers.
Its [original immutable deployment](https://github.com/colophon-group/jobseek/actions/runs/37260481257)
completed and promoted v0.13.934. B0 activated at epoch181 and fresh admission
staged 5,884 monitors/2,052 detail boards through the original wrapper. Activation
correctly refused two monitor boards already owned by B0. Its original pending
recovery also refused that inert overlap, leaving the lane contained with the
pending identity retained. No ordinary production adoption is claimed.

v0.13.935 therefore also fixes cancellation of a never-active staged plan.
Cancellation still verifies canonical B0 configuration, current source/epoch,
exclusive SQL/host scope, the original B0 queue audit and exact projection CAS.
It leaves canonical data, B0 ownership and inert staged SQL history unchanged.
Activation and active retirement continue to refuse overlap. The corrected
production admission must exclude every verified B0 board before staging.
Recover the retained source934 pending identity through the original wrapper
and its supported exact-source/image compatibility administrator, prove the
restored stack, then continue the corrected full-cohort adoption.

v0.13.935 adds configured DOM and JSON-LD Browser detail execution to the existing
ordinary Go worker. It shares the B0 pinned mutual-TLS protobuf client and pure
held-document parser, preserving the existing canonical enrichment, posting
writer, publisher policy and Browser queue settlement. Four renderer slots keep
the service bound. Request defaults and bounded retries match existing browser
navigation. Verified B0 boards retain exclusive browser ownership, and startup
refuses a Browser ownership plan without the protected renderer selector.
Unsupported actions, custom transports, linked/fallback descriptions and iCIMS
iframe recovery retain their current owner until the native replacement exists.

Real PostgreSQL/Redis tests cover rendered canonical title/description/salary/
location fields, pending descriptions, publisher reservations before HTTP status,
fresh inactive/reserved state, disappearance, transient failures, cancellation,
changed authority and Browser settlement. Cold reversal covers interrupted and
committed-before-ACK details, actual source hosts and canonical deadlines;
overlapping B0 ownership refuses publication. The combined queue race suite
passed in48.996s and the combined worker race suite passed in114.674s. Shared client/navigation and
supervisor race tests pass, and37 required Python ownership/cutover tests pass.
[Portable candidate evidence](evidence/go-native-rendered-details-candidate-2026-10-05.json)
records this scope. This candidate is not selected in production. Remaining
enabled profiles, runtime consumers, resource proof and full Python retirement
remain required before completion.

## Full-fleet ownership staging correction — 2026-10-05

The full migration goal remains active. [PR #10307](https://github.com/colophon-group/jobseek/pull/10307)
merged nested sitemap support as `8dc9c5b99b38bbfcb1ff22442b36935d44b14126`.
Its original immutable deployment and promotion succeeded. All six baseline
writers were independently healthy and armed before the supported B0 activation
selected `cdom` at epoch 179; a naturally scheduled render and canonical commit
were observed. Ordinary Go ownership is still unselected.

Fresh source/receipt-bound admission covers 5,884 monitor boards and 2,052 detail
boards, including 335 direct DOM monitors and 486 DOM detail boards, across about
1.04 million scheduled postings. Thirty-one boards with cached route mismatches
retain their existing owner. Admission is an observation, not queue authority.
The original full-fleet stage rejected twice with the fixed `stage: deadline`
diagnostic. Twenty-four read-only SQL telemetry samples saw individual board
snapshots and no blocking sessions; no timeout, lock or ownership bypass was used.

v0.13.934 batches administrative canonical configuration reads in one sorted
`FOR SHARE` snapshot and cached configurations in 256-board Redis pipelines. It
retains the existing transaction/host bounds, lease/epoch barriers, canonical
profile validators and exact ownership bytes. Staging, fresh staged inspection,
cold activation validation and cold retirement use these snapshots; every normal
runtime claim, request and write continues to revalidate current authority.
The cumulative snapshot string bound is 64 MiB. Cold monitor deadline/receipt capture
also uses one SQL read without rewriting historical receipts.

A private reproduction using 6,512 unique fresh canonical/cache configurations
staged 5,884 monitors and 2,052 detail boards in 1.83 seconds and passed inspection.
The new 257-board regression proves one SQL snapshot and two Redis pipelines,
equivalence to individual observations, missing/disabled admission refusal and
safe disabled-board retirement. Full queue and worker race passed in 47.058 and 124.281 seconds, Go vet and
module checks passed, and all 35 required Python ownership/cutover tests passed. [Portable candidate evidence](evidence/go-native-ownership-batching-candidate-2026-10-05.json)
records this scope; candidate delivery and production adoption remain unproved.

The next delivery is this fleet-stage correction through required CI and the
original immutable deployment. Retire B0 and clear selectors through the original
protocol, then verify the complete baseline **before merging** to avoid deploying
against a selected-environment drift. Re-activate on the promoted source, repeat
fresh admission, select the exact ordinary plan and prove naturally scheduled
monitor/detail writes and supported cold reversal. The separate rendered-worker
expansion is preserved as work in progress. Remaining enabled profiles, runtime
consumers, whole-lane resource proof and final Python retirement still remain.

## Nested sitemap continuation — 2026-10-05

[PR #10306](https://github.com/colophon-group/jobseek/pull/10306) merged the direct
DOM runtime as `e435e120ea0d14382a34da19812e6fb32fde0155`, with Required CI,
installed-image parity and Crawler Deploy Gate green. Reviewed and merged trees
match. Its original immutable deployment is building; source930 baseline and B0
remain serving, and ordinary ownership remains unselected after staging rejection.

v0.13.933 extends the existing `sitemap.explicit-urls/v1` worker to resolve nested
indices before publishing an inventory. It retains Python job-child preference,
cycle suppression, strict transient retries and missing/invalid child handling.
Exhausted required shards preserve prior postings. Header/meta reservations bind
the actual child URL and policy before status handling. The compiled traversal
bounds are eight levels, 200 same-origin children, three child attempts, 50 MiB per
document and 55 MiB aggregate. Discovery, cross-origin children and unsupported
transport/configured XML retry overrides still require native replacement.

Full real PostgreSQL/Redis race suites passed (queue 48.264s, worker 115.396s),
parser/transport race and Go vet/module checks passed, and all 35 required Python
ownership/cutover tests passed. Real owned tests verify complete canonical union,
urgent details, failure preservation, empty confirmation, cycles and child policy.
[Portable index evidence](evidence/go-native-sitemap-index-runtime-candidate-2026-10-05.json)
records candidate scope. Production adoption and full migration remain unproved.

## Combined direct DOM monitors and details — 2026-10-05

The full migration goal remains active. v0.13.932 combines direct static DOM
monitor inventory with v0.13.931 DOM detail execution in the existing Go worker.
Both use the existing sealed public document transport, parser, ownership plan,
canonical writers and Redis settlement. Static monitors preserve Python URL
joining, raw Unicode identities, CSS selectors and include/exclude filters;
complete inventories create stubs and urgent details, with existing four-miss
absence and spaced 404/410 disappearance handling. Publisher reservations retain
the actual redirected resource and policy before HTTP failure handling. Rendered,
paginated, proxy and unsupported configured routes retain their current owners.

Full real PostgreSQL/Redis race suites passed (queue46.691s, worker105.931s),
DOM parser/transport race passed (2.931s), Go vet/module checks passed, and all35
required Python ownership/cutover tests passed. Frozen Python fixtures cover15
URL joins and5 listing/filter cases; real owned monitor tests cover canonical
inventory, retry exhaustion, disappearance, publisher opt-outs and settlement.
[Portable combined DOM evidence](evidence/go-native-dom-runtime-candidate-2026-10-05.json)
records the scope. Production DOM adoption and full migration are not claimed.

Source930 immutable deployment/promotion succeeded. Independent baseline readback
verified all six exact-image writers healthy and restart-armed at epoch176.
Original B0 activation succeeded at epoch177. Fresh full-fleet admission screened
7,885 enabled boards and reconciled949,287 actual scheduled detail routes, admitting
5,549 monitors and1,566 detail boards (929,334 scheduled owned postings). This
includes276 Join monitors,284 sitemap monitors and265 Join detail boards;30 cache-
mismatched detail boards retain legacy ownership. Original ordinary staging
rejected before retaining a plan. No ownership was activated or manually changed;
existing writers continue serving. The same source/epoch/config identity was
rechecked before a supported retry. An isolated local PostgreSQL/Redis reproduction
stages and inspects the complete5,549/1,566 configuration plan. Installed-image
diagnostics independently verify source-bound environment, protected cohort,
read-only database schema/epoch and Redis availability, yet staging still rejects.
v0.13.932 adds bounded administrative phase/category logging without exposing
credentials, board configuration or SQL messages; focused executable contracts
pass (5.570s). Natural source930 ordinary writes remain unproved. Continue delivery and diagnose the staging rejection without bypassing
original authority, timeout or deployment protocols.

## Native direct DOM delivery and merged Join/sitemap release — 2026-10-05

[PR #10305](https://github.com/colophon-group/jobseek/pull/10305) merged v0.13.930
as `9d62e57778387a3807bfc0d6125058e248941a70`. Required CI, installed image parity
and Crawler Deploy Gate passed; the merged and reviewed trees match exactly.
[Immutable deployment](https://github.com/colophon-group/jobseek/actions/runs/37249185517)
completed deployment/promotion. Fresh admission and original ordinary activation
remain required before claiming production Join/sitemap writes.

v0.13.931 adds `dom.direct-detail/v1` to the existing native worker. It calls the
existing Go public document fetcher and DOM parser directly, then uses independent
posting-host ownership, shared enrichment, canonical SQL persistence and Redis
settlement. Cookie handshakes, redirects, configured retries/public headers,
gone-URL classification, supported text encodings, large static documents and
actual-resource publisher reservations retain their contracts. Linked documents,
fetch rewrites, secondary extraction, proxy and rendered routes keep existing
coverage until their native contracts are implemented.

The full real PostgreSQL/Redis race suites passed (queue47.795s, worker94.318s),
parser/document/exporter race and Go vet/module checks passed, and all35 Python
ownership/cutover tests passed. Focused regressions additionally prove canonical
fields/deadlines, permanent gone and transient/empty failures, zero repeat fetch
on settlement, and positive reservations received after concurrent inactivation.
[Portable DOM candidate evidence](evidence/go-native-dom-detail-runtime-candidate-2026-10-05.json)
records the implemented scope. No production DOM adoption or full migration
completion is claimed. Continue the remaining delivery list below without adding
another orchestration layer.

## Combined Join/sitemap delivery and production API proof — 2026-10-05

The goal remains full production migration to Go and self-hosted Lightpanda,
with mandatory Python orchestration and Playwright/Chromium retired after complete
replacement and supported reversal. Reuse the current parsers, native worker,
queue, persistence, exporter and drain. Preserve enabled boards and useful offline Python.

Source928 original activation succeeded after the standard legacy reaper requeued
23 simple and five browser expired monitor claims at its normal fifth-strike limit;
no dead letters or authority/retry overrides were used. Independent readback verified
ten exact-image running writers with restart policies armed and eight healthy HTTP
endpoints. The cohort owns 4,989 monitors and 1,301 detail boards (947,631 scheduled
postings at admission); 30 cache-mismatched boards retain legacy detail ownership.
Natural samples show 385 matching monitor deadlines, successful SmartRecruiters and
Workable monitor cycles, six settled details across JSON-LD/Workday, canonical fields
and descriptions, and no deadline mismatches. This sample does not establish API
detail writes, full-fleet fetch completion or comparable resource savings.
[Portable API928 production evidence](evidence/go-native-api928-owner-2026-10-05.json)
records the scope. Original outgoing ordinary/B0 reversal completed. Independent readback at
00:37 UTC verified all six baseline writers healthy and restart-armed, receipts
and projections absent, selectors cleared and no current active fences at epoch176.

v0.13.930 combines the tested Join monitor/configured detail implementation with
native explicit sitemap URL sets in one release. Sitemap uses the existing Go parser
and sealed HTTP, with the existing retry/body bounds, normalization and UTM stripping.
URL filters reuse the existing Python-compatible Go regex engine. Complete inventories
create URL stubs and urgent details; failures and unsupported indices preserve prior
inventory. Positive publisher policy precedes status handling. Nested filter/detail
configuration binds semantically across PostgreSQL and Redis key ordering; changed
values remain bound. Unsupported transport/discovery/rewrite options retain coverage
through their existing owners.

The full real PostgreSQL/Redis worker race suite passes (98.955s), as do the queue,
parser/exporter race suites, Go vet/module checks and all 33 affected Python tests.
[Portable combined candidate evidence](evidence/go-native-join-sitemap-runtime-candidate-2026-10-05.json)
records the implementation and limits. Fresh production admission and natural
Join/sitemap writes follow the immutable release; they are not claimed yet.

Continue with the existing delivery list: remaining sitemap discovery/indices,
Booking and rich identities; generic configured HTTP/Lightpanda monitor/detail
coverage; remaining mandatory Python runtime maintenance/deploy consumers;
whole-lane resource and reversal verification; then runtime-only asset removal
after the rollback window. Full migration remains incomplete until these pass.

## Deployed API release and native Join continuation — 2026-10-05

The full migration goal remains active. [PR #10302](https://github.com/colophon-group/jobseek/pull/10302)
merged the combined native SmartRecruiters/Workable monitor and detail release as
`340a2ef93ccbd62184589120925d9fbc9213f9a0` (v0.13.928). Its exact reviewed tree was
retained, and [deployment/promotion](https://github.com/colophon-group/jobseek/actions/runs/37242300794)
succeeded. Complete outgoing source926 ordinary/B0 reversal restored all six
legacy writers healthy and restart-armed, cleared selectors/receipts/projections,
and left no current active fences at epoch174. Incoming source928 baseline image
and writer identities were independently verified, then original B0 activation
succeeded at epoch175 with all seven HTTP health endpoints passing.

Fresh canonical/cache and actual scheduled posting-route reconciliation admits
4,989 monitors and 1,301 detail boards: 668 JSON-LD, 431 Workday, 126 SmartRecruiters
and 76 Workable. The monitor additions are 123 SmartRecruiters and 74 Workable.
Coverage is 947,631 scheduled postings at admission; this is not a fetched count.
Thirty boards retain legacy detail ownership because their queued caches disagree.
The initial staging command rejected after retaining the staged plan. The installed
native inspection command independently verified the exact source/epoch, plan,
projection and counts, without changing a timeout or authority check. Original
combined cold activation refused after SQL leases expired, retaining its pending
identity and the full cold lane. Twenty-eight expired tokenless monitor claims
remain; recovery uses the standard legacy reaper once scheduled reconciliation
releases the maintenance lock. Native API serving has not yet been proved.

v0.13.929 integrates the existing Go Join pagination and configured nextdata detail
parser directly into the same native worker. Complete monitor inventories create
URL stubs and schedule details separately; incomplete pagination writes no partial
inventory. Existing four-miss absence and spaced 404/410 board disappearance
handling remain. Parallel responses retain the actual reserved page, source and
policy, including after redirects. Configured detail fields use shared enrichment,
canonical persistence, pending descriptions and posting-host deadlines. Failed or
empty extraction preserves prior content, and positive publisher policy precedes
inactive-posting settlement. Unsupported field mappings retain legacy ownership. The full real PostgreSQL/Redis
race suites pass (queue46.309s, worker82.923s), as do the exporter, Join parser,
Go vet/module checks and all 33 affected Python ownership/cutover tests.
[Portable Join evidence](evidence/go-native-join-runtime-candidate-2026-10-05.json)
records both configured monitor and detail contracts.

Continue the existing delivery list below: remaining sitemap/Booking, rich
identities and configured browser/HTTP routes; direct maintenance/deployment
consumers; comparable whole-lane resource and reversal proof; then removal of
mandatory Python orchestration and Playwright/Chromium after the rollback window.

## Full delivery continuation and native API details — 2026-10-05

The goal is full production migration to Go and self-hosted Lightpanda. Completion
requires enabled crawling and canonical writes, maintenance, export/drain and
deployment consumers to run through native paths; production Python orchestration
and Playwright/Chromium dependencies must be retired where replacements are
reasonable. Keep useful isolated offline Python. Finish supported production
cutovers, natural output/queue correctness, comparable resource measurement and
full reversal before removing legacy runtime assets.

[PR #10300](https://github.com/colophon-group/jobseek/pull/10300) merged the UUID
cursor correction as `5621926e5301865d914b696bd488190481cefc4a` (v0.13.926).
The supported [deployment](https://github.com/colophon-group/jobseek/actions/runs/37237145385)
built immutable images and completed deployment/promotion. Original source925 B0
rollback restored all six legacy writers, removed receipts/projections and cleared
all selectors at epoch172. Original source926 B0 activation then succeeded at
epoch173, with source/image/receipt verification and all seven HTTP health endpoints
passing. Fresh canonical/cache and actual posting-route admission selected 4,792
monitor boards and 1,099 detail boards (668 JSON-LD, 431 Workday), covering 850,181
scheduled owned postings. This is admission coverage, not a completed-fetch count.
Original combined activation succeeded after the standard legacy reaper recovered
21 expired tokenless claims and dead-lettered one at its normal fifth strike;
no retry-policy override or manual queue/ownership edit was used. All ten exact-image
writers were independently running with `unless-stopped`, all eight HTTP endpoints
passed, and both ownership receipts matched the SQL/Redis plan at epoch173.
Natural processing then recorded nine settled details across both profiles, six
new content receipts and no SQL/Redis deadline mismatch. Samples retained titles,
locations and descriptions, including salary currency where present. The original
full ordinary/B0 reversal precedes the next immutable API release.
[Portable production evidence](evidence/go-native-combined-owner-2026-10-05.json)
records source, identity, actual coverage and limits.

v0.13.927 adds SmartRecruiters and Workable details through the existing native
worker. It reuses their Go parsers, verified direct HTTP, canonical ownership,
shared enrichment/description persistence and SQL/Redis settlement. Independent
detail adoption retains legacy monitors. Full board configuration hashes and
actual posting URLs are rechecked at claims and writes. SmartRecruiters retains
one GET; Workable retains one API GET and a single Markdown fallback on 429.
Non-200 empty results, malformed/transport failures and positive header/meta
publisher reservations preserve existing content and settlement behavior. Cold
retirement restores each posting's actual host and canonical deadline. Existing
Workday and monitor plan bytes remain compatible. Configured proxy/browser,
insecure TLS or additional pipeline steps remain legacy until their contracts
are implemented. This candidate does not activate these API owners. Full real PostgreSQL/Redis
race suites pass (queue47.720s, worker77.313s), including actual TLS requests,
canonical fields/descriptions, publisher policy, legacy coexistence and full cold
retirement. [Portable candidate evidence](evidence/go-native-api-details-candidate-2026-10-05.json)
records verification scope and limits.

Continue in delivery order: activate and verify combined native JSON-LD/Workday;
ship API details and native SmartRecruiters/Workable monitors; implement remaining
enabled HTTP/browser profiles; then retire remaining mandatory Python consumers
and runtime assets after complete replacement/reversal proof. The exporter and R2
drain already execute in Go in production; reuse them. Avoid introducing another
scheduler, ownership registry or persistence framework.

## Native API monitor continuation — v0.13.928

The existing ordinary worker now handles SmartRecruiters' default publication URLs
and Workable's URL inventory in process. It calls the existing Go provider modules
through verified HTTP, preserving pagination, bounded retries, opaque Workable
page tokens and counted Markdown/public-API fallback. Complete inventories use the
existing URL-only writer and schedule details separately. A later-page failure
writes no partial inventory and never enters provider-gone handling. Positive
header/meta reservations persist their actual resource, source and policy. Both
providers retain the existing four-miss absence threshold and lifecycle settings.
The sealed plan binds the full canonical configuration, including separate detail
options; legacy Workday/monitor plan bytes remain compatible.

SmartRecruiters opt-in requisition/location identity modes remain outside this
URL-only profile; migrate their existing rich identity writer next. Browser or
proxy monitor routes and unknown options retain their existing ownership. This
candidate adds no scheduler, queue, ownership registry or persistence framework.

Continue with one delivery list:

1. Merge and deploy native API details/monitors through the required gates after
   the complete outgoing reversal; reconcile all enabled canonical configurations
   and actual posting routes, activate the eligible combined cohort, and verify
   natural canonical fields, descriptions, deadlines and publisher policy.
2. Wire existing Go sitemap, Join and Booking modules into the same native worker;
   migrate remaining rich identity and configured HTTP/browser families using
   existing Go extraction and the self-hosted Lightpanda service. Keep every
   enabled board covered while completing replacement authority.
3. Audit production scheduling, maintenance and deployment commands; replace any
   remaining mandatory Python consumers with direct Go paths. Keep exporter/R2
   drain on their existing Go implementations and retain useful offline Python.
4. Verify the complete production lane, comparable resources and cold reversal;
   after the rollback window, remove mandatory Python, Playwright/Chromium and
   unused runtime assets. Full migration remains incomplete until these pass.

## Delivered source925 and cursor correction — 2026-10-04

The full migration goal remains active. [PR #10297](https://github.com/colophon-group/jobseek/pull/10297)
merged v0.13.925 at `a744c09f6e3a6c5dfdf0392f4428642541a850b1`.
The supported deployment [37232650405](https://github.com/colophon-group/jobseek/actions/runs/37232650405)
succeeded on attempt 2 after the original source924 full ordinary/B0 reversal.
All six legacy writers were independently restored healthy and restart-armed,
ownership receipts/projections absent and selectors cleared at epoch170.
The first deploy correctly refused an active outgoing B0 receipt; it was retried
through the original workflow after complete reversal, with no override.
Source925 B0 activation then succeeded at epoch171 with all seven HTTP health
endpoints passing. The larger ordinary JSON-LD owner has not been activated.

Production query verification found that `ORDER BY id` resolved to the selected
`id::text` alias. Even with migration0039 installed, the two sampled larger board
histories still took 5.7 and 11.0 seconds to sort before returning 64 rows.
v0.13.926 qualifies `ORDER BY job_posting.id`, preserving UUID keyset ordering and
using the existing complete `(board_id,id)` index directly. All six read-only
candidate queries used that index without sorting, taking 0.3–55.2 ms.
These sequential samples have different cache states and do not prove fleet
throughput or savings. [Portable query evidence](evidence/go-native-jsonld-cursor-query-2026-10-04.json)
records exact source, queries, plans and sample limits. A real PostgreSQL
regression checks the actual runtime query's index traversal rather than relying
on a small functional fixture to reveal the full-board sort.

Continue by merging/deploying the cursor correction through required checks,
using original B0 reversal and selector clearing first. Reconcile fresh monitor,
Workday and JSON-LD configurations and all scheduled posting routes at the new
source/epoch; stage the combined cohort through the existing immutable plan and
activate through the original full cold protocol. Verify natural JSON-LD
fields, publisher-policy results, description/deadline conservation and indexed
selection before expanding the next provider slice. Deliver SmartRecruiters and
Workable using their existing Go parsers and the shared native runtime, then
remaining enabled browser/monitor profiles and mandatory Python maintenance,
scheduling and deployment consumers. Measure comparable whole-lane resources,
prove full replacement/reversal and retain the rollback window before removing
production Python, Playwright, Chromium and legacy runtime assets. Preserve
every enabled board and useful isolated offline Python tools.

## Native JSON-LD detail candidate — 2026-10-04

The full migration goal remains active. v0.13.925 extends the existing native
worker with direct JSON-LD details using the existing parser, verified HTTP
transport, shared enrichment/persistence, opaque attempts and original SQL/Redis
ownership plan. Detail boards can retain their legacy monitors. Every claim and
write resolves the actual canonical posting board and source URL; no per-posting
ownership registry is introduced. Workday and monitor-only plan bytes remain
compatible. The JSON-LD board binding covers its actual public source hosts;
queue selection and requests always use the canonical posting's actual host.

Fresh read-only admission at source924/epoch169 selects **668 JSON-LD detail
boards covering 309,092 scheduled postings**, including DOM, sitemap, iCIMS and
other monitor families. Five additional supported boards retain legacy detail
ownership because queued/inflight caches differ from canonical routes. Browser,
proxy, insecure TLS and configured fallback/enrichment steps remain excluded
until their native execution contracts are delivered. The admission does not
activate an owner or prove that postings have already been fetched.

Real PostgreSQL/Redis race suites pass for independent native ownership, legacy
monitor coexistence, canonical write exclusion, publisher opt-outs, HTTP failure
classes, extraction/enrichment, SQL/Redis settlement and complete cold retirement.
Selection rotates past full candidate batches and uses bounded Redis pipelines.
JSON-LD metadata hashes normalize nested SQL/Redis key ordering. Production query
plans showed 64-row traversal taking up to 10.7 seconds while sorting complete
board histories; migration 0039 adds `(board_id, id)` concurrently, including
inactive/future receipts needed for interrupted ACK recovery. Its supported
upgrade, downgrade and re-upgrade are checked against the private migrated
PostgreSQL fixture. Production query cost after this index remains to be verified.

[Portable candidate evidence](evidence/go-native-jsonld-detail-candidate-2026-10-04.json)
records admission and verification limits. Production remains v0.13.924 at
`a90e8635dc0b0389a3af92d41467a35836a3bc36`, epoch169, with 4,792 native
monitor boards and 431 native Workday detail boards. The latest independent
readback observes 67 settled detail receipts with matching SQL/Redis deadlines,
zero mismatches and all eight HTTP health endpoints passing.

Continue with exact-head Required CI, Crawler Deploy Gate and installed-image
parity; then exercise the original source924 full cold reversal before supported
source925 deployment. Reconcile fresh canonical/cache/actual routes, retain the
existing admitted Workday detail cohort, add the eligible JSON-LD cohort through
the original cold protocol, and verify exact source/images, all writers, natural
outputs and deadlines. Validate indexed query cost and whole-lane freshness.
Continue remaining enabled API/browser/monitor profiles and Python maintenance,
scheduling and deployment consumers. Measure comparable whole-lane resources,
exercise reversal and establish the rollback window before retiring production
Python, Playwright and Chromium. Useful isolated offline Python tools may remain.

The protected cohort schema is unchanged:

```json
{"version":"jobseek.ordinary.cohort/v1","monitors":["<eligible native monitor board UUID>"],"details":["<eligible independent JSON-LD detail board UUID>","<eligible Workday monitor board UUID>"]}
```

JSON-LD detail UUIDs may be independent of `monitors`; Workday detail UUIDs still
must belong to the native monitor cohort. Full-board canonical configuration and
actual posting routes remain mandatory admission checks.


## Active Workday detail delivery — 2026-10-04

The full Go and Lightpanda migration goal remains active. Deliver all enabled
crawler profiles and runtime consumers, then remove production Python,
Playwright and Chromium after replacement and supported reversal are proven.
Preserve useful isolated offline Python tools and every enabled board.

[PR #10269](https://github.com/colophon-group/jobseek/pull/10269) delivered
v0.13.924 at source `a90e8635dc0b0389a3af92d41467a35836a3bc36`.
Required CI, Crawler Deploy Gate, installed-image parity and the ARM whole-lane
check passed at reviewed head `233819360a1ddfc018200d7c68f5423ff65bc8d4`.
The reviewed and merged Git trees match. Supported deployment
[37223070602](https://github.com/colophon-group/jobseek/actions/runs/37223070602)
succeeded; main CI and installed-image parity also passed.

The original full cold activation is complete at routing epoch 169. Go owns
4,792 monitor boards and Workday details for 431 boards. Fresh admission bound
542,659 scheduled postings to those detail boards and reconciled actual posting
URLs with queued/inflight routing. All ten writers run the expected immutable
images with `unless-stopped` restart policies; all eight HTTP health endpoints
pass. The ownership count does not mean every posting has already been fetched.

The outgoing source923 ordinary/B0 reversal passed before deployment. Incoming
activation initially refused 43 expired tokenless legacy monitor claims. The
installed protected maintenance reaper requeued 38 simple and five browser
claims, with zero dead letters or missing configurations. The original exact-plan
activation retry then succeeded. No installed wrapper, receipt, lock, fence or
queue was manually patched.

[Portable delivery evidence](evidence/go-native-workday-details-2026-10-04.json)
records exact source/image/plan identities, coverage, checks and readiness.
Ten completed natural detail receipts have matching SQL/Redis deadlines and
zero mismatches; native metrics report ten successful detail executions and
no transport, execution or claim errors. Current-epoch monitor completions
have not yet been observed.
Twenty-five otherwise eligible Workday detail boards retain legacy ownership
because actual queued cache routes differed. Configuration exclusions and all
other unowned profiles remain serviced by the existing runtime.

Continue in this order:

1. Verify natural native detail commits and canonical/Redis deadlines. Repair
   the 25 excluded queued-cache routes through existing protected maintenance,
   then re-admit them with fresh canonical and queued-route evidence.
2. Extend the existing native detail dispatch with the already implemented Go
   JSON-LD parser/transport and remaining enabled API/browser profiles. Reuse
   current queue, enrichment, persistence and ownership contracts; deliver each
   coherent coverage increase through required checks and supported deployment.
3. Replace remaining production Python maintenance, scheduling and deployment
   consumers with existing native implementations where available. Preserve
   full-stack readiness and canonical output/freshness throughout delivery.
4. Compare whole-lane CPU/RAM, density and attributable cost; exercise supported
   cold reversal of native detail ownership and establish the rollback window.
   Remove Python/browser runtime assets when replacement coverage and reversal
   are established. Full migration is complete only after these exits pass.



## Detail delivery candidate — 2026-10-04 12:37 UTC

The full migration goal remains active. v0.13.924 now implements native Workday
detail dispatch through the existing ownership plan, original claim Lua, opaque
attempts, canonical posting/board gates, shared Go enrichment and description
writer. Posting IDs remain distinct from board IDs. Fresh publisher opt-outs,
inactive postings, HTTP failure classes, Workday 404/S22 empty results, host
circuits, cancellation and future canonical deadlines retain their existing
behavior. Restart recovery acknowledges committed results without another GET
or SQL write. Metrics distinguish monitor and scrape execution.

Explicit selection uses the existing protected cohort file and installed
`ordinary-go-cutover.sh stage <absolute-file>` command. A historical JSON array
still selects monitors only. To select eligible detail boards, use:

```json
{"version":"jobseek.ordinary.cohort/v1","monitors":["<board UUID>"],"details":["<same eligible Workday board UUID>"]}
```

`details` must be a unique subset of `monitors`. Staging rechecks canonical and
cached configurations and returns an immutable plan/projection identity plus
the detail-board count; it cannot activate an owner. Legacy detail writes resolve
the actual canonical posting board under the shared authority barriers. Forged
cached board metadata cannot grant a write to an owned posting.

The original cold retirement now conserves detail deadlines for interrupted
claims, committed results before ACK, reaped results before ACK and inactive
postings. It retains SQL content and receipts, revokes old attempts, persists
Redis before SQL retirement and supports exact retries after SAVE failure.
Foreign canonical posting boards refuse before projection or queue mutation.
Retained acknowledged receipts are probed in batches so historical detail work
does not require a network round trip per posting during reversal.

Production remains the verified v0.13.923 monitor owner at epoch 167. Native
details are a delivery candidate, not yet deployed. Next actions are:

1. Finish local verification, publish v0.13.924 and require exact-head Required CI,
   Crawler Deploy Gate and installed-image parity before merge.
2. Retire the outgoing ordinary/B0 owners with the original source923 drivers,
   verify full legacy restoration, then promote source924 through the supported
   immutable-image deployment.
3. Reconcile fresh canonical/cached monitor and actual posting routes. Stage
   eligible Workday detail boards explicitly, activate through the original full
   cold protocol and verify source/image/receipt identity, all writers and health,
   natural detail fields and canonical/Redis deadlines. Preserve unsupported
   posting routes on legacy ownership until their replacements are delivered.
4. Continue remaining enabled detail/browser profiles and runtime maintenance
   consumers; measure comparable whole-lane resource costs and establish the
   rollback window before retiring production Python, Playwright and Chromium.

## Production continuation — 2026-10-04 11:57 UTC

The full migration goal remains active. PR #10267 is merged as
`95ec59850d327a267ff1f23e54753160ca085114` and deployed as v0.13.923
by supported deployment run `37196598961`, attempt 2. The original cutover
activated the exact 4,792-board monitor plan at B0 epoch 167, including 495
Workday boards. All ten writers are independently verified running with
`unless-stopped` restart policies and exact digest-pinned images; native and
legacy health endpoints pass.

The initial activation refused surviving tokenless Redis leases after SQL lease
expiry. The installed, source/image/receipt-bound maintenance reaper restored
91 simple and six browser monitor tasks with zero dead letters or missing
configs and no SQL, owner or B0 changes. The original exact-plan retry completed.
No receipt, wrapper, fence or lock was patched.

Natural serving evidence now records 246 settled canonical/Redis deadline
matches and zero mismatches. Workday has three completed native monitor attempts,
two successful. Eight of nine profiles have natural completions; Personio has
none in this observation. Unacknowledged outcomes remain tracked. See
[evidence](evidence/go-native-workday-monitors-2026-10-04.json).

Workday detail execution remains Python in production. The v0.13.924 worktree
has committed canonical detail gates and shared native enrichment/persistence.
The current continuation implements explicit detail membership in the same
immutable ownership plan, original Lua claims, and a Python write guard that
resolves the actual canonical posting board. PostgreSQL tests prove native-owned
details cannot reach the legacy writer through forged routing metadata; native
owned claim/persistence/settlement tests also pass. Runtime dispatch and complete
supported detail retirement must be finished and verified before publication.
The remaining goal includes other enabled detail/browser profiles, runtime
maintenance/deployment consumers, resource measurements and Python retirement.

The goal remains full delivery of the Go and Lightpanda crawler migration,
including retirement of production Python, Playwright and Chromium after
replacement coverage and supported reversal are established. Useful isolated
offline Python tools may remain. Preserve every enabled board.

## Latest continuation — 2026-10-04, 11:08 UTC

[PR #10267](https://github.com/colophon-group/jobseek/pull/10267) merged the
v0.13.923 Workday monitor at source
`95ec59850d327a267ff1f23e54753160ca085114`. Required CI, Crawler Deploy Gate
and installed-image parity passed at reviewed head
`8c1bade44d083a73049a8f780514bbae62cc28da`.
[Release build 37196598961](https://github.com/colophon-group/jobseek/actions/runs/37196598961)
has built both immutable images. The full source922 cold reversal passed:
original installed drivers retired ordinary ownership, restored 22 B0 details
and cleared the exact selectors. Independent readback verifies all six legacy
writers healthy and restart-armed, exact source/images, retained retired SQL
plan, routing epoch 166, absent ownership receipts/projections and zero current
active fences. The [portable reversal evidence](evidence/go-native-family922-restoration-2026-10-04.json)
records this proof. The supported deployment is retrying after that reversal;
the merged monitor is not yet an active Workday owner.

Later natural v0.13.922 observations cover all eight admitted profiles and 526
completed deadlines matching SQL and Redis. All five previously observed
completed but unacknowledged attempts recovered naturally after lease expiry
at their unchanged canonical deadlines. Additional unacknowledged outcomes
still occur. Three newly created native postings have title, locale, HTML and
matching uploaded description hashes; two also have resolved location and
technology fields. These observations do not establish comparable fleet costs.

The v0.13.924 detail continuation uses the existing opaque attempts, canonical
read/write fences, shared native enrichment, description writer and terminal
deadline receipts. Actual PostgreSQL/Redis tests cover posting IDs distinct from
board IDs, normalized fields, staged upload hashes, exact settlement and fresh
publisher reservation. Empty detail results preserve content and visibility.
Exclusive detail selection and runtime dispatch remain required before native
detail activation. Extend the existing ownership and queue contracts for that
work; do not introduce a parallel registry or per-posting ownership projection.

## Deployed readback — 2026-10-04, 10:09 UTC

[PR #10266](https://github.com/colophon-group/jobseek/pull/10266) delivered
v0.13.922, source `b9e853a75d80cd2fa5d97d37ec58822259eedd4b`.
The supported deployment and original installed cutover driver activated all
4,297 admitted native monitors at epoch 165. Independent readback verifies
the exact immutable images, active SQL/Redis/host identities, eight healthy HTTP
endpoints and all ten runtime writers running with `unless-stopped` restart
policies. The ordinary process started at 09:58:45 UTC. A readback after its
ten-minute lease timeout observes 267 completed deadlines matching canonical
SQL and Redis, with no settled deadline mismatch or claim error.

The [portable evidence](evidence/go-native-rich-fleet-2026-10-04.json) records
per-profile completion and resource snapshots. Five runs were reported as
unacknowledged; recovery remains under observation. One posting write reached
its deadline. Natural samples preserve title, locale, description and uploaded
content across six profiles; Personio and Teamtailor have not run naturally
in this interval. Resource snapshots do not establish comparable fleet costs.
The source921 supported full cold reversal passed before this promotion;
the expanded source922 cold reversal remains due before the next promotion.

The next code slice implements Workday URL discovery, owned monitor persistence
and separate detail scheduling. Fresh current canonical/cache admission accepts
495 of 496 Workday boards, including configured tenant sites and bounded deep
pagination/facet unions. Actual PostgreSQL/Redis race tests cover insertion,
relisting, lost-enqueue repair, four-miss disappearance and publisher opt-outs.
Workday detail jobs keep their canonical posting owner and existing queue;
native detail ownership and execution are still required. The remaining board's
explicit TLS override is real: its handshake fails against the pinned CA bundle.
Preserve that board while implementing compatible transport. No Workday
production ownership or full migration completion is claimed by this candidate.

Continue by delivering the Workday monitor slice, moving its detail pipeline
onto the existing native queue/write authority and enrichment, then migrating
the remaining HTTP/API and Lightpanda browser profiles. Complete enabled-profile
coverage, freshness/output and queue checks, comparable whole-lane measurements
and supported reversal before retiring production Python and browser assets.

## Historical Greenhouse family — 2026-10-03, 20:40 UTC

All 2,570 enabled Greenhouse canonical/cache profiles passed fresh admission and
are active Go owners on v0.13.910, source
`c7b1dcf2c4d25a2677aaf70073928a9e940fd441`, at routing epoch 151.
[Deployment 37137484149](https://github.com/colophon-group/jobseek/actions/runs/37137484149)
completed promotion. The ordinary plan is
`4478835d261551535a125980ddd4c6df53feff3b8fdee78bcd479cfd5d5b3b93`;
its Redis projection is persistent. Installed activation succeeded and restored
all ten services with their restart policies; all eight HTTP endpoints passed.

All-member readback verified 2,570 canonical boards and atomic Redis states.
For 51 completed current-epoch receipts, native, canonical and Redis deadlines
agree. Sequential metrics recorded 50 successful monitors, 1,461 posting touches,
2,216,516 response bytes and zero claim, transport, execution or cancellation
errors. Four retained epoch-149 receipts are historical evidence. No new/relisted/
gone transition was observed yet; natural enrichment/description generation,
fleet freshness, resources and expanded-cohort reversal remain outstanding.

The supported rollout exposed an inactive stopped old-image ordinary container
and 44 expired tokenless legacy Redis leases. The exact stopped container was
removed with no volume removal; the installed maintenance wrapper ran the existing
reaper once under read-only SQL barriers. It requeued 42 simple and two browser
leases, with zero dead letters or missing configurations and no SQL ownership or
B0 mutations. The unchanged installed activation driver then succeeded. These
are concrete workflow compatibility fixes to carry into the next release, not
reasons to add another recovery framework.

The next unpublished slice wires the existing Ashby/Lever parsers into the same
native discovery, enrichment, persistence and settlement contracts. Their saved
census contains 935 Ashby and 195 Lever boards. Two require separate detail
scraping. This slice must preserve explicit tokens containing dots/spaces,
URL inference, EU routing, pagination and configured disappearance floors before
fresh production staging. No Ashby or Lever native adoption is claimed.

At the later production readback, all 2,570 member states still passed. There
were 192 completed current-epoch receipts with matching native/canonical/Redis
deadlines, 192 successful monitors, 10,822 touches and one natural disappearance
transition. Error counters remained zero and all eight HTTP endpoints passed.
This extends the earlier proof; it still lacks a natural new-description sample.

The Ashby/Lever candidate now admits 1,128 of the 1,130 separately captured
canonical/cache records: 934 Ashby skip profiles and 194 Lever skip profiles.
The other two require detail scraping. Sixteen frozen actual Python requests
cover token precedence, dots/spaces and EU routing. Real native PostgreSQL/Redis
pipeline tests cover verified HTTP, enrichment, employment/location fields,
description byte storage and pending upload, lifecycle and matching queue
deadlines. Later Lever page failure and publisher reservation both settle with
no partial inserts/delistings; policy evidence retains the actual later page.
These are candidate checks, not production Ashby/Lever ownership.

The observations below are historical and superseded by this deployed family.

## First native ordinary owner — 14:45 UTC

The supported exact adoption succeeded after database-clock lease expiry.
All four selected boards now have native ownership at epoch 149 and completed
Go-owned monitor receipts. Their four naturally due API requests succeeded,
touched 513 existing postings and settled one-hour canonical deadlines that
match Redis. All eight HTTP health endpoints and source/image/receipt bindings
passed. No claim, transport, execution or cancellation errors were reported;
no native lease remains. All 513 existing descriptions remain stored and
uploaded. No new/relisted/gone posting transition was observed in this sample;
new description/enrichment generation still needs applicable natural evidence.

The [portable evidence](evidence/go-native-ordinary-continuation-2026-10-03.json)
preserves this observation and the earlier recovery/census. Full migration
remains active. The next bounded code change permits a fresh-epoch owner only
after prior ordinary owners have retired. Historical attempt receipts remain
intact and lose write authority through their old epoch. Another active owner
or served history at the current epoch still refuses adoption. Complete the
existing supported old-owner/B0 retirement and fresh B0 activation before
using this change to expand the cohort.

## Greenhouse family continuation

[PR #10238](https://github.com/colophon-group/jobseek/pull/10238) and
[PR #10240](https://github.com/colophon-group/jobseek/pull/10240) are merged with
Required CI and the actual Crawler Deploy Gate green at their final heads.
They deliver natural lease waiting, the partial posting-lease index and fresh
adoption after an older owner has retired. Their release builds completed;
promotion refused while the v0.13.905 ownership receipts remain active. The
healthy existing lane continues serving until the larger cohort's approved
immutable image is ready and supported retirement clears those receipts.

A read-only capture at 14:55 UTC contains all 2,570 enabled Greenhouse canonical
configurations and their cached projections. The original native factory
admitted 2,520; the remaining 50 need existing Python token precedence and URL
inference, including custom board URLs with explicit tokens and an ignored
legacy `board_token` field. The expanded factory admits all 2,570 with zero
unsupported canonical/cache profiles and zero binding mismatches. No production
configuration was changed. Frozen results from the actual Python token function
and request construction cover all 50 variants plus 29 boundary cases. Native
request checks enforce the exact fixed Greenhouse API endpoint; private
PostgreSQL/Redis checks exercise staging, canonical/cache binding and owned claims.
These are admission and request-contract results; production ownership is still
four boards. Fresh supported staging/adoption must revalidate current state.

## Earlier verified deployment and recovery

[PR #10229](https://github.com/colophon-group/jobseek/pull/10229) delivered the
native Greenhouse token/skip worker, transactional persistence and queue
settlement, exclusive ownership, and installed full-stack cutover/recovery.
Required CI and Crawler Deploy Gate passed at its head. Release v0.13.905,
source `eb991eb3997e66e4f05d327405a399f26c4a4644`, was promoted by
[deployment 37123792391](https://github.com/colophon-group/jobseek/actions/runs/37123792391).
Its immutable images are:

- Crawler: `ghcr.io/colophon-group/jobseek-crawler@sha256:2b61a57b0abde3540d12ec96996b8d4fbba55ebd26039b2278457d81d777e2c1`.
- Browser: `ghcr.io/colophon-group/jobseek-crawler-browser@sha256:ba1032d7ebe28b40b53477c35150bb8d1b79fedee7d71d52e7f2df5968f873c4`.

B0 is active at epoch 149 with cohort `cdom`. First ordinary adoption selected
four enabled canonical Greenhouse token/skip boards: 1-800 Contacts, Brex,
Duolingo and Figma. Admission failed before ownership publication. PostgreSQL
retained a staged plan; no ordinary SQL owner, active fence or Redis ownership
projection was established in that initial attempt. The later supported retry
established the four owners described above.

Two concrete admission problems were observed: legacy SQL leases survive
process cancellation for up to ten minutes, and the unchanged posting-lease
guard exceeded its ten-second timeout scanning approximately 5.87 million
postings without a lease index. [PR #10238](https://github.com/colophon-group/jobseek/pull/10238)
adds bounded natural lease expiry and migration 0038's partial lease index.
Neither change clears leases or relaxes the exclusive SQL barriers.

The exact reviewed index was prepared through the installed maintenance
wrapper using the already approved immutable v0.13.905 image. It completed in
17.904 seconds, with no canonical or ownership changes. The unchanged native
SQL admission then passed in 0.090 seconds, holding all three barriers and
observing zero live legacy leases. The installed `recover-pending` command
succeeded, removed the inert adoption receipt, and restored all nine B0/legacy
services. Source/image readback, all seven HTTP endpoints and every restored
restart policy passed. B0 recorded three naturally scheduled commits at epoch
149, zero render/executor failures, zero inflight and zero dead records.

These are bounded component and recovery observations. They do not establish
ordinary canonical output, fleet coverage, whole-lane savings or final Python
retirement. Both corrective PRs subsequently passed their required checks and
merged; their images have not replaced the currently serving v0.13.905 lane.

The [sanitized production evidence](evidence/go-native-ordinary-continuation-2026-10-03.json)
includes a read-only SQL inventory at 14:30 UTC: 7,885 enabled boards, of which
5,084 have active board status. It groups every enabled board by monitor type
and effective monitor/detail browser flags. This includes 2,570 Greenhouse,
935 Ashby and 195 Lever monitors. These counts describe remaining coverage
obligations; they do not claim migrated native ownership.

## Delivery order

1. Continue natural Greenhouse proof for new descriptions/enrichment, lifecycle,
   deadlines, queue conservation, publisher policy and supported retirement.
   The complete enabled Greenhouse family is already deployed and active.
2. Expand ordinary ownership through the existing worker and queue contracts.
   Start with remaining enabled Greenhouse profiles, then reuse the existing
   Go Ashby and Lever parsers. Add only the profile-specific metadata, detail
   and persistence contracts needed by the current enabled cohort.
3. Reconcile enabled SQL boards and effective monitor/detail/browser policies
   against native capability. Complete remaining API, HTTP, DOM, sitemap and
   browser routes using existing Go modules and Lightpanda. Configured CSV
   counts are not a live enabled coverage denominator.
4. Replace mandatory Python runtime consumers, including startup, scheduling,
   health, migrations and maintenance entrypoints. Keep offline workspace,
   labelling and frozen comparison tools separately packaged where useful.
5. Prove complete canonical/output/freshness/queue behavior and comparable
   whole-lane CPU, RAM, density and attributable cost. Exercise supported final
   cutover and cold reversal with exact source/image identities. Observe the
   rollback window, then remove production Python and legacy browser assets.

Use the existing immutable deployment, ownership and maintenance surfaces.
Each continuation should remove a concrete remaining migration obligation;
new generic orchestration or recovery frameworks are not prerequisites.
Honor current holds and other operators' locks. Update this checkpoint with
the actual first ordinary owner and subsequent enabled-profile coverage.


## Multi-provider continuation and UKG retirement — 2026-10-06

Release 0.13.946 (Jobylon + NextData, 80 additional configurations) deployed
through the original immutable workflow. Native ordinary ownership activated at
epoch 195 for 6,595 monitors and 2,475 detail boards. Independent observation at
00:18 UTC verified 165 completed monitor attempts, six completed detail attempts,
canonical posting/description effects, all ten services healthy and restart armed,
and no bounded processing diagnostics. Lightpanda 1.0.0 remains the latest stable
release and its qualified renderer stays pinned by digest.

The supported retirement before the Inline + Beisen batch failed after natural
legacy lease expiry. The wrapper retained its retiring receipt and contained the
crawler lane. Read-only source-bound inspection found unchanged monitor bindings
and exact SQL/cache agreement. Seven UKG legacy monitors had added their public
listing URL and, on two boards, host/tenant/board identifiers after native detail
ownership was staged. The existing Avature learned-portal case covers the eighth
changed detail binding. No successful cold reversal is claimed yet.

The amendment permits UKG detail retirement only when added values are exactly
derivable from the originally bound first-party HTTPS board URL. Removing only
those additions must reproduce the prior configuration hash. Other changes,
previously configured values, foreign URLs and cache-only updates still refuse;
runtime claims continue to lose authority on a changed configuration. Real
PostgreSQL/Redis regression coverage verifies canonical deadlines, retained content,
queue conservation, durable retirement and rejection of unrelated changes.

After required checks on the amended head, use its original immutable build and
the existing pre-pull/compatibility administrator surfaces to retire source 946
through its unchanged wrapper. Verify the restored complete stack, roll back B0,
clear selectors through the original helper, and independently verify the baseline
before the original full rollout. Never edit the retained plan, fences, leases or
receipt to obtain recovery.

Port several types per release. The Inline + Beisen batch qualifies 124 additional
configurations. The next isolated batch groups RSS, sitemap and Personio; its
current configuration screening qualifies 36 RSS, 19 sitemap and all ten remaining
Personio boards. That work and its validation remain in progress. Complex browser,
proxy, filtering/collision and remaining extraction variants stay in full scope.
Complete enabled coverage, maintenance/deployment consumers, whole-lane resource
proof, cold reversal and final production Python retirement remain required.


## RSS + sitemap + Personio grouped continuation — 2026-10-06

The next release groups three existing provider types. Screening the remaining
source-946 configurations qualifies 36 RSS, 19 sitemap and all ten Personio boards,
65 additional configurations. The new item profiles reuse the existing Go
Teamtailor/SuccessFactors/Personio parsers, sealed transport, rich normalization,
canonical writer and lifecycle. Configured JSON-LD/DOM detail scrapers remain
bound without implying enrichment: an explicit scraper with no enrich list keeps
the feed's authoritative description and fields, matching the actual processor.

Sitemap and RSS URL filters/replacements run before native normalization or any
canonical write. Replacements preserve the actual identity published to PostgreSQL,
the detail cache and the urgent queue; they grant no additional fetch authority.
Ignored legacy URL aliases and sitemap count metadata stay bound. Nested detail
configuration keys use semantic JSON binding, preserving array/step order and
separate browser detail assignment.

The actual Python processor supplied 14 URL-policy cases and 32 assignment cases.
Real worker suites exercise skip, JSON-LD and DOM assignments, translation/fallback,
partial XML/later-page failures and publisher reservations. Four real sitemap
cases verify rewritten canonical identity, cache, deadline and urgent queue.
The full real PostgreSQL/Redis worker race suite passed in 224.352 seconds; all
57 required Python runtime/ownership tests passed, with six optional cases skipped.
The complete real PostgreSQL/Redis queue race suite passed in 120.752 seconds. The executable and installed
image identity expand from 42 to 45 profiles.

This is candidate qualification, not deployed source-948 authority. Complete
fresh admission from canonical/cache posting routes after deployment, natural
processing and supported cold reversal. Remaining generic/paginated/browser/RMK/
legacy RSS variants, job filters/collision/security policy, and sitemap proxy/TLS/
cross-origin/rescrape cases remain in the full migration scope. Preserve every
enabled board while completing those replacements. The
[candidate evidence](evidence/go-native-feed-provider-candidate-2026-10-06.json)
records the current qualified subset and explicit remaining work.

## Grouped API delivery, 2026-10-06

PR [#10335](https://github.com/colophon-group/jobseek/pull/10335) merged MokaHR,
AlmaCareer and Eightfold together as release **0.13.949**, source
`c7f789faef866c0fd77921750095f1a6253c707d`. All required checks, installed image
proof and ARM64 measurement passed at reviewed head
`3e3510e66b06e6a67240abf2ceaf112c3ec91963`; the merge tree matches that head.
The original full immutable deployment is run **37417474358**. Its successful
build produced crawler digest `42531604eea973acd5fa0b5e893b86c016c204e146ac7990156b7931f3e07cec`
and browser digest `52401861991705f52464c962e0527c3f172c562fedccf86ee707560be2cd9de3`.
Host promotion and renewed native admission must be verified separately.

Before that merge, the source 948 ordinary owner retired through its original
wrapper, B0 rolled back through its original wrapper, and selectors were restored.
Independent readback at epoch 198 proved the retired plan, absent owner receipts
and routing projections, zero native claim tokens, six healthy exact-image legacy
services, and a healthy claimant isolated in dark mode. Four interrupted immutable
write receipts remain audit records; they confer no retired-owner authority.

The next grouped release **0.13.950** adds **Softgarden, UKG, BambooHR and
Recruiter.co.kr** together. The registry census admits all 55 current monitor
configurations and their 41 configured detail bindings: 15 Softgarden JSON-LD,
16 UKG embedded, 10 BambooHR HTTP API, and14 Recruiter.co.kr monitor-hydrated skip
assignments. These counts describe local configuration eligibility. Production
admission must use fresh canonical/cache and actual scheduled-route evidence.
The four providers reuse existing claims, rich/URL-only persistence, detail engines,
publisher-policy handling, failure/gone decisions and cold retirement. New HTTP
reference cases preserve UKG pagination, BambooHR description filters and tenant
retirement redirects, Recruiter.co.kr hydration retries/KST dates, and Softgarden
custom URL patterns. Generic Softgarden 404 remains a failure, preserving Python's
behavior. Delegated descriptions retain scraped bytes and locales during refresh.

Continue with grouped provider and variant ports while immutable CI/deployment
runs. Then complete enabled-profile coverage, remaining runtime consumers,
natural freshness/queue conservation and comparable whole-lane cost measurements;
exercise supported cold reversal and the rollback window; retire production
Python, Playwright and Chromium after replacement authority is established.
The full migration goal remains **active and incomplete**. Official releases were
rechecked October 6: **Lightpanda 1.0.0** remains the latest stable qualified pin.

## Grouped generic variants, 2026-10-06

Release **0.13.951** continues with four engines in one iteration: DOM static
URL pagination and rewrites, generic RSS feeds, inline alternate fetches with
public cache-bypass headers, and explicit API-sniffer detail enrichment.
The current registry admits 62 DOM pagination configurations, 20 DOM rewrite
configurations, 11 generic RSS feeds, 4 inline cache-bypass routes and 43 API
enrichment assignments. DOM counts overlap. These are local eligibility counts,
not new production ownership. The installed runtime advertises 56 profiles.

Twenty-five actual Python HTTP cases compare request URLs/order, retries,
transformed identities, XML fields, fallback fetches, publisher reservations
and later-page failure outcomes. Twelve real canonical PostgreSQL/Redis cases
cover all four engines; twelve additional cold reversal cases preserve deadlines,
interrupted/committed audit receipts and stale-writer refusal. Rendered DOM
pagination remains excluded until its complete inventory contract is ported.
Cold-owner errors now log fixed phase/reason enums without upstream data.

Production source 949 completed its original immutable deployment and reactivated
Lightpanda 1.0.0 at B0 epoch 199. Its fresh admission reconciled 1,417,415 scheduled
detail routes and selected 6,849 monitors and 2,515 detail boards. The first
ordinary activation was rejected. The original pending recovery restored the
full fleet; independent exact-source/image readback proved active B0, an inert
staged ordinary plan, absent ordinary projection/receipt and no native ordinary
claim tokens. A supported exact-plan activation retry is in progress. Do not
infer ordinary ownership from staged admission or candidate parser tests.

Continue grouped remaining provider/browser/filter variants, verify deployed
ownership and natural processing, finish runtime/deployment consumers and
whole-lane measurements, then establish reversal/rollback evidence before
retiring production Python, Playwright and Chromium. Preserve isolated useful
Python reference tooling. The full migration goal remains active and incomplete.

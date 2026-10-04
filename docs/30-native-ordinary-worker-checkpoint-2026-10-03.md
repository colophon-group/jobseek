# Native ordinary worker continuation, 2026-10-03

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

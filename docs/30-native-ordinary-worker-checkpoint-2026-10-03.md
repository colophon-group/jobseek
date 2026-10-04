# Native ordinary worker continuation, 2026-10-03


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

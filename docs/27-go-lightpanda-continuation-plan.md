# Go and Lightpanda migration continuation plan

## Delivery objective — revised 2026-10-04

Deliver the migration in full: production Go workers and self-hosted Lightpanda
or proven Go HTTP/API routes must replace every enabled crawler profile, then
remove production Python, Playwright and Chromium after the supported rollback
window. Preserve useful isolated offline Python tools and every enabled board.

Judge progress by deployed native owners, enabled profiles migrated and mandatory
production Python consumers removed. Check canonical/database/publisher behavior,
freshness and queue conservation on real jobs, and compare whole-service resources
and cost. Add a fixture or abstraction only for a specific changed production
contract or observed failure. Plans and synthetic evidence are supporting work.

## Native RSS worker candidate — v0.13.919

The ordinary Go worker now implements the existing direct Teamtailor RSS preset
with a `skip` detail assignment, reusing its feed parser and the installed native
claim, enrichment, persistence and settlement path. Complete 100-item pagination
is required before inventory writes. A later-page failure preserves existing
postings; publisher reservation binds the actual response resource. Canonical
and cached configuration must independently pass native admission before ownership.
The offline registry contains 141 candidate RSS/skip boards; this count does not
establish production admission or serving ownership. Other RSS presets and detail
assignments retain their existing execution until their native implementation lands.

Verification covers real PostgreSQL/Redis claims, rich title/description/salary/
location fields, description upload eligibility, listing/gone effects, a failed
second page, publisher reservation and canonical/Redis deadline conservation.
Both ordinary Go modules pass race tests and vet; the original Python ownership
and cutover contracts pass. This is an implementation candidate, not a deployment.

The same candidate fixes the observed provider starvation: a rejected/no-op
claim for a throttled domain continues through other domains in the existing
64-member scan. Each attempt still uses the original atomic claim checks;
first-time and recurring-detail priority cannot be bypassed. The real two-provider
regression failed before this correction. The current candidate admission over
source17 production inputs admits 4,250 profiles, including 137 Teamtailor and 197
SuccessFactors; deployment and a fresh incoming-release census remain required.

## Native SuccessFactors worker candidate — v0.13.919

The native ordinary worker now reuses the existing streaming SuccessFactors Go
parser for direct Google/category RSS feeds with a `skip` detail assignment.
Explicit feeds retain precedence over the derived `/googlefeed.xml` URL. Only
absent/default or `feed` variants are admitted; legacy, RMK, XML variants and
configured filtering/detail overrides retain their existing owners. There are
218 offline feed/skip candidates, including unsupported overrides; actual
canonical/cache admission determines the eligible production cohort.

Real PostgreSQL/Redis cycles verify rich title/location/salary/technology fields,
exact stored description and pending upload, listing/gone effects, generic 404
failure, publisher reservation and deadline/lease conservation. Malformed XML
following an emitted item publishes no partial inventory. Both ordinary Go
modules pass full race tests and vet. The implementation follows the Teamtailor
candidate and requires required gates and a supported source-bound deployment.

## Current production checkpoint — 2026-10-04

The full migration goal remains active. v0.13.917 is promoted at
`990165526e328cddd05c271053ffd903d7a7a671` by
[deployment 37167862702 attempt 2](https://github.com/colophon-group/jobseek/actions/runs/37167862702).
The live environment, selected release, success marker, immutable crawler/browser
images and installed original wrappers agree. Source-bound B0 `cdom` is active
at epoch 157. All 3,916 admitted rich/skip boards now have native ownership:
2,570 Greenhouse, 934 Ashby, 194 Lever, 114 Recruitee and 104 Pinpoint. The fresh
canonical/cache census had zero configuration mismatches; three detail profiles
remain excluded. All eight HTTP readiness endpoints pass, the exact Redis
projection persists, and every admitted SQL/Redis member matches. The latest
readback has 532 matching completed deadlines and three stale Redis retry
deadlines for committed receipts (Cybrid, May Mobility, Xometry); SQL receipts
and canonical deadlines agree. The supported retirement must repair those
scores before the next ownership expansion. Newly admitted providers still
have zero completed runs. A real PostgreSQL/Redis regression reproduces native
selection returning after its oldest throttled domain; the candidate fix tries
another provider in the same bounded batch while preserving Lua priority guards.

The earlier Greenhouse-only run produced two natural new postings with active
canonical state, title/locales, completed fences, exact stored HTML hashes and
uploaded scalar description hashes. Its two committed SQL receipts with stale
Redis retry scores were repaired by the original supported retirement. The B0
rollback retired epoch 156, selectors were cleared, and B0 was reactivated at 157.
The 3,916-board activation retained its exact pending identity on expired legacy work;
the existing reaper restored 87 simple and 4 browser entries after scheduled
reconciliation finished. The same activation plan/projection retry passed full
readiness and restart arming. No fence/lease clearing or lock bypass was used.
Four Python worker processes still serve remaining profiles; all 7,885 enabled
boards remain enabled and full retirement is unfinished. See the
[portable production evidence](evidence/go-native-family917-production-2026-10-04.json).

[PR #10257](https://github.com/colophon-group/jobseek/pull/10257) merged the bounded
retirement correction after Required CI, the actual Crawler Deploy Gate and
installed host/image parity passed, including different immutable outgoing/admin
sources. Older retained attempts are accepted only when their immutable retired
plan owned the monitor. Foreign old/current/future work still refuses; no SQL
lease or retained fence was cleared to force retirement. The reviewed newer admin
retired original v0.13.913 with its outgoing wrapper and B0 receipt unchanged.
After an interrupted projection handoff, exact retirement recovery completed the
original full readiness/restart-arming and stopped-native-container cleanup.
Original B0 rollback retired epoch 154, then the exact selectors were cleared
before the full v0.13.917 deployment.

[PR #10258](https://github.com/colophon-group/jobseek/pull/10258) supplied an
authenticated immutable image cache operation with ephemeral CI package access.
The image downloaded, but its assumed OCI revision label was absent. The actual
RepoDigest and compiled Go source passed the separate maintenance preflight.
[PR #10259](https://github.com/colophon-group/jobseek/pull/10259) fixes that observed
workflow check while retaining compiled-source verification in the retirement
driver before writer shutdown. Both workflow changes passed required checks.

Activation must follow the installed contract. Wait for legacy SQL leases to
expire naturally. If expired tokenless Redis monitors prevent admission, keep
the exact pending receipt and cold lane, use the existing source-bound reaper,
then retry **activate with the same plan/projection hashes**. `recover-pending`
**cancels** a pending activation and restores the whole legacy stack; it does
not resume activation. It completes retirement when the retained receipt is
retiring. Respect scheduled reconciliation's mutation lock and let its bounded
slice finish. The existing reaper restored 85 simple and 11 browser monitors
without dead letters, missing configs, SQL ownership writes or B0 changes.

Continue by observing naturally due processing across the admitted providers and
checking fields, description/R2/publisher behavior and strict deadlines. Deliver
the tested Teamtailor RSS/skip worker candidate, then native remaining RSS,
Workday and generic HTTP/Lightpanda monitor/detail profiles. Replace mandatory
Python startup, scheduling, maintenance and deployment consumers. Compare
whole-lane resources/cost; exercise supported cold reversal and its observation
window. Remove production Python, Playwright, Chromium and runtime-only legacy
assets after those checks, preserving useful isolated offline Python and every
enabled board. Measure progress by serving native owners, enabled profiles
migrated and mandatory runtime Python consumers removed.

## Earlier production observations — 2026-10-03

Production v0.13.913, source `f90d8244c66a0ca07635ab9bb08adea4b0b6c462`,
was promoted by [deployment 37158271320 attempt 2](https://github.com/colophon-group/jobseek/actions/runs/37158271320).
The supported original drivers restored Lightpanda `cdom` and all 2,570 Greenhouse
native owners at epoch 153. The fresh canonical/cache census admitted all 2,570
without config mismatches. The 23:13 UTC readback verified every SQL/Redis member,
persistent projection, all eight HTTP readiness endpoints and 61 completed
current-epoch deadlines. Native metrics recorded 59 successful monitors, 3,235
posting touches and zero claim, transport, execution or cancellation errors.
See the [portable production evidence](evidence/go-native-family913-production-2026-10-03.json).
At 23:14 UTC the worker had 110 successes, one timeout and two current boards
with `native_processing_failed`; safe diagnostics identified `posting_write` /
`deadline` failures. Claim errors remained zero. Diagnose these specific failures
and repeat strict deadline checks before expanding ownership. Natural
new/description/enrichment/gone behavior and whole-service freshness/cost also
remain unfinished.

The prior v0.13.910 observation is historical: by 22:22 UTC it had 604 successes,
12 failed tasks, 29 timeouts, 31 unacknowledged tasks and 39 claim errors. Its
strict 21:29 check found three completed SQL deadlines with earlier Redis retry
deadlines. [PR #10249](https://github.com/colophon-group/jobseek/pull/10249), now
deployed in v0.13.913, removes repeated immutable ownership-document decoding
and adds safe processing-phase diagnostics. Pool sizes, timeouts, retries and
claim authority retain their existing contracts. Continue observing current
failures and strict deadline parity before expanding production ownership.

Native Ashby/Lever rich routes from [PR #10246](https://github.com/colophon-group/jobseek/pull/10246)
and four Go Compose health probes from [PR #10248](https://github.com/colophon-group/jobseek/pull/10248)
are deployed in v0.13.913. Actual Docker metadata verifies all four installed
probe commands; the ordinary and browser service processes still execute Python
for their remaining profiles. Ashby/Lever production native owners remain zero;
the earlier 1,128-profile candidate census must be refreshed before admission.

[PR #10251](https://github.com/colophon-group/jobseek/pull/10251) is merged at
`6a56adeccbad8122ca8490876240f0d06d4a0cdd` with native Recruitee/Pinpoint rich
execution. Required CI, the actual Crawler Deploy Gate, installed image parity
and 16-arm ARM64 admission passed before exact-head merge. The 218 eligible
candidate profiles and one excluded Floryn detail profile are candidate evidence;
v0.13.914 is not deployed and these providers have zero native owners. Thirteen
actual Python request fixtures bind endpoint precedence; real PostgreSQL/Redis
checks cover rich fields, description uploads, provider-specific 404s, publisher
reservation and full-inventory settlement. Refresh the census against the next
approved release before staging a combined rich-provider cohort.

[PR #10253](https://github.com/colophon-group/jobseek/pull/10253) is merged at
`86a4dfdf9d15d2e97df3518edb50f5501ea06859` and replaces the NW and Umantis
forward deployment repair commands with Go. It embeds the exact existing SQL/Lua contracts and retains
UUID/dedup behavior, foreign-owner refusal, Umantis two-pass verification and
idempotent partial-batch recovery. Historical rollback still uses its original
prior-image Python command. The same slice repairs one observed retirement seam:
after complete legacy/B0 readiness and restart arming, remove only the exact
stopped ordinary container before deleting its retiring receipt. Preserve the
old image for rollback. Required CI, the actual Crawler Deploy Gate and installed host CI passed before
merge; the new original retirement cleanup and old-image retention are verified.
v0.13.915 is not deployed at this checkpoint.

The 23:31 UTC v0.13.913 check found 465 matching completed deadlines and two
completed SQL deadlines with earlier Redis retry scores and no live tokens.
Metrics recorded 460 successes, two failed tasks, six timeouts, three
unacknowledged tasks, three claim errors and one gone outcome. The strict check
failed; preserve those observations instead of treating them as transient proof.
A real PostgreSQL/Redis reproduction shows a specific bottleneck: one blocked
canonical board consumes the sole authority connection and makes an independent
board hit its deadline. Merged v0.13.916 uses at most five connections,
matching the default five worker slots, and limits the claim mutex to cursor
updates. Transactions retain their existing time limits and per-operation
SQL/Redis/epoch/lease/canonical checks. Five repetitions verify independent-board
progress, refusal of the blocked disabled board and exactly one owned lease from
five simultaneous callers. Full queue/worker race suites and vet pass. This
fixes the reproduced bottleneck; production deadline resolution remains a check
after the approved combined release is deployed.

The v0.13.913 cold handoff initially refused 68 expired tokenless Redis monitor
leases after the SQL leases expired naturally. The existing installed maintenance
reaper requeued 63 simple and five browser entries with no dead letters, missing
configs, SQL writes or ownership/B0 changes. The original activation retry then
completed. Retained write fences from retired epoch 151 remain historical;
current admission was bound to epoch 153. Never clear SQL leases or partially
restart writers to bypass admission.

Continue from the current checkpoint above. Build the approved corrected combined release,
use the original complete retirement/B0/deploy/reactivation contracts, then
adopt fresh eligible Ashby/Lever/Recruitee/Pinpoint profiles. Finish every
remaining provider, URL/detail and browser profile plus mandatory Python runtime
consumers. Keep every enabled board scheduled. Prove full-service output,
publisher policy, freshness, queue conservation and comparable resources/cost;
exercise supported cold reversal and its observation window, then retire
production Python, Playwright, Chromium and runtime-only legacy assets. The full
delivery goal remains active until that retirement is complete.

## Earlier registry delivery — 2026-10-03

Crawler v0.13.903, source `b026cc597486b4d39831b93a85e06bcda1d9f04c`,
is promoted in production. [PR #10220](https://github.com/colophon-group/jobseek/pull/10220)
removed the Python launcher from forward deployment registry sync and compatible
normal CSV publication. Both now call `go-typesense-exporter --sync-registry`
directly with the existing transaction, publication implementation, immutable
image, committed environment and read-only data mount. The CSV runtime-contract
gate remains. Historical bootstrap/recovery and full rollback retain their
supported prior-image CLI until those rollback targets expire.

[Deployment 37108784568](https://github.com/colophon-group/jobseek/actions/runs/37108784568)
completed including promotion. Actual Go sync committed and scheduled all 7,885
enabled boards and completed publication for 6,019 companies. Live source/image
identities agree across the environment, selected release and success marker.
Supported `cdom` rollback at epoch 145 restored 22 ready tasks, dropped one terminal
task and left zero write fences. After clearing/restaging the 25 exact selectors
under the mutation lock, supported activation restored `cdom` at epoch 147.
Worker, browser, exporter, drain and native claimant HTTP health passed; the
initial cohort snapshot had 22 ready, zero inflight/dead tasks and zero new native
commits. This slice removes two Python launchers; it adds no ordinary native
owners and does not establish full-service freshness or cost parity.
See the [portable production evidence](evidence/go-registry-native-entrypoints-production-2026-10-03.json).

Required CI and the actual Crawler Deploy Gate passed before exact-head merge.
The changed deployment/publication/rollback contracts passed 62 focused tests;
private UTF-8 PostgreSQL/Unix Redis registry races and installed mounted dry-run /
missing-mount refusal also passed. Normal CSV publication has installed and host
contract evidence; a separate natural production data-only publication has not
yet been observed.

Recovery fixture expansion has taken too much effort while production ordinary
and browser workers remain Python. Deliver in this order:

1. Connect and deploy the existing `greenhouse.token-skip/v1` native worker.
   The main-based [PR #10229](https://github.com/colophon-group/jobseek/pull/10229) contains the
   existing processing/persistence code, three ownership/fencing migrations,
   exact legacy exclusion, installed-image source binding, an opt-in native
   Compose service and a first-owner host driver. First adoption and retirement
   preserve the current B0 epoch and receipt. Real private PostgreSQL/Redis tests
   cover native claims, commits, settlement, projection durability and the actual
   compiled administrative command. Shell tests cover complete startup and
   containment after image, administrative, readiness, restart-arming and signal
   failure. Native ordinary execution and installed-binary execution passed in
   CI on the initial `9b6edecc62a77eb7e6c25c4f3e2be0b55993c3f2` candidate;
   its full crawler suite found four shutdown mocks requiring the new ownership
   startup dependency. Those fixtures now pass with the affected pipeline/queue
   suite (170 tests). Exact active retries preserve interrupted leases and repeat
   no SAVE, allowing the unchanged complete native stack to recover normally.
   Required CI, installed-image parity and ARM64 B0 lane measurement subsequently
   passed at `3955c1ee57d1473f5d4c334aaef48b2ca83d67c2`. Cold retirement now
   restores interrupted owned monitors from PostgreSQL deadlines, revokes their
   Redis tokens and preserves completed receipts, canonical rows and the B0
   incarnation. Real PostgreSQL/Redis race tests cover claim-before-SQL, stale
   attempts, disabled members, commit-before-ACK, reaping, SAVE failure/retry and
   refusal before any effect. Both full native queue and worker suites pass
   locally. The installed-image CI job now also exercises the original host
   driver with actual containers and the native process, including failed
   readiness containment and complete recovery. Its legacy/B0 health services
   are fixtures; production coverage, freshness and resource claims still need
   real production observations. Wait for those updated-head checks, then
   deliver the default-off service and use the supported production cold
   cutover. No production ordinary owner has been selected yet.
   Foundation PR #10207 and worker PR #10210 remain preserved drafts; their joint
   transition framework and unwired completion fixture are not prerequisites.
2. Connect already implemented Go provider/enrichment routes to native
   claim/fetch/enrich/persist/reschedule execution for remaining enabled effective
   profiles. The fresh production inventory is 8,019 total / 7,885 enabled boards,
   including 522 with browser flags; reconcile effective configs and queue routes
   before claiming coverage. Preserve every enabled board throughout migration.
3. Replace mandatory worker/browser startup, Python healthchecks, migration and
   provider-repair commands, cutover and scheduled maintenance consumers. Remove
   production Python/Playwright/Chromium after coverage, canonical effects,
   publisher policy, freshness, queue conservation, comparable whole-service
   cost, supported rollback and the observation window pass. Preserve useful
   isolated offline Python tooling.

Report deployed native owners, enabled profiles migrated and runtime Python
consumers removed. Add a fixture only for a named production failure or missing
acceptance check. Preserve exclusive ownership, deployment holds, the active-B0
deployment guard and complete quiesced rollback. The full migration objective is
unfinished; this current section supersedes historical next-action priorities below.


Reviewed 2026-09-30 against `origin/main`
`c571568167b7abf7dfa80a7f9c73bcbb29d49cb2` and current GitHub PR/check state.
This is the forward plan for completing the crawler service migration. Start
with the saved salary candidate, then remove the Python orchestration and
persistence boundaries while completing coverage of enabled boards. Preserve
Python where it remains useful outside the production crawler runtime.

Current production is recorded in the [September 30 location checkpoint](28-go-location-resolver-checkpoint-2026-09-30.md).
Native executor implementation and remaining ownership gates are recorded in the
[September 30 native B0 candidate checkpoint](29-native-go-b0-executor-checkpoint-2026-09-30.md).
The initial review below is historical; use newer verified operational evidence first.

Current delivery: shared salary/location and native B0 persistence are promoted
at crawler v0.13.902, revision `b75ccb9456bf29c9477f9747c0c2cc3908ad79bb`.
Native B0 PR #10204 passed final 16-arm admission and exact-head required gates,
completed coordinated crawler/renderer releases, and now runs Go at epoch 145.
Supported native cold reversal/restoration (143→144→145) and one naturally due
Bunq fenced commit with exact stored hash/upload/schedule continuity are proven.
The [native checkpoint](29-native-go-b0-executor-checkpoint-2026-09-30.md) binds
immutable images, observations and limits. Whole-service cost and fleet output
are still incomplete. The independent ordinary queue foundation is saved in
draft [PR #10207](https://github.com/colophon-group/jobseek/pull/10207), with real
Linux AMD64/ARM64 Redis contracts passed. It selects no ordinary worker. Next,
complete claim/write/settlement fencing and native ordinary processing, then
every enabled profile and runtime consumer before final Python/Chromium
retirement. The full migration goal remains active.

This initial review made no production changes and did not reread the live hosts.
Production details below are the latest recorded checkpoint, corroborated by
the latest successful crawler deployment, rather than a new health attestation.
Refresh live release, ownership and selectors before any operational change.

## Initial checkpoint and source precedence

| Surface | Verified or recorded state | Resume from |
| --- | --- | --- |
| Saved implementation | Draft [PR #10177](https://github.com/colophon-group/jobseek/pull/10177), `fix-crawler/go-salary-extraction`, head `b6274d6c74bc96a219870cf4abb1b5d8bb1dcf3c`; candidate v0.13.899, not merged or deployed | [Salary handoff at the saved head](https://github.com/colophon-group/jobseek/blob/b6274d6c74bc96a219870cf4abb1b5d8bb1dcf3c/docs/24-go-lightpanda-salary-checkpoint-2026-09-28.md) |
| Candidate checks on 2026-09-30 | Required CI, Bindings and conformance, and Installed image parity succeeded; Crawler Deploy Gate failed intentionally while draft | Refresh checks after any base/head change; a successful gate reconciliation job is not a green Crawler Deploy Gate status |
| Last production release | v0.13.898, revision `20b4031ccc7e3056c6ca10b55b68bd5562c0c720`; [deployment 36467631537](https://github.com/colophon-group/jobseek/actions/runs/36467631537) completed including promotion | [Language checkpoint and production evidence](24-go-lightpanda-resumption-plan.md#production-checkpoint-2026-09-28--shared-go-language-detection-v013898) |
| Last recorded cohort | cdom active at epoch 133; 22 selected schedules, 17 already unqueued; 25 exact selectors staged; C2 Kandou dark | Live receipt and selector readback before mutation; use the current owner and epoch, not these saved values blindly |
| Last recorded renderer | September 28 nightly; source `fb2b1c865e90ccf67379aa1f303e2b5f5e8f95c0`; image `sha256:72b73991cc4a6820263970ccf07367b5d9193eb37cb9d742701fb78ea0104a21` | [Pinned manifest](../pilots/go-lightpanda/lightpanda-release.json); refresh official asset identity at resumption |

The salary handoff exists on the draft branch, not current main. Its immutable
GitHub link makes resumption independent of the previous local checkout. The
candidate records 2,664 compatibility cases across 18 currencies, 202 focused
Python tests, Go race/vet/module checks, and exact comparison of 512 stored
descriptions from 265 boards and 14 locales. These establish compatibility;
natural production salary output and database effects remain outstanding.

Use live committed release/receipt state first, then exact deployment and
deployed evidence, then saved candidate/check state. The September 24 opening
paragraphs in [#7935](https://github.com/colophon-group/jobseek/issues/7935) and
[#8648](https://github.com/colophon-group/jobseek/issues/8648) predate the
September 28 evidence. Their historical hold and ownership statements do not
establish current host state. No open `deployment-hold:crawler` issue was found
in this review; recheck immediately before any deployment and honor any new hold.

The original [migration design](23-go-lightpanda-migration.md) and the lower
historical sections of [the resumption log](24-go-lightpanda-resumption-plan.md)
remain references. The 10M-board projection, generic queue-v2 rewrite and
arbitrary sample/savings quotas are not prerequisites for the next slice.
Issue [#7966](https://github.com/colophon-group/jobseek/issues/7966) is
owner-closed; its retirement criteria remain useful, and closing it did not
establish completed migration. Keep it closed.

## What already moved and what remains

The deployed checkpoints record Go ownership of configuration/queue sync,
Typesense publication, backfill, reconciliation, schema and count refresh,
maintenance slices, and R2 drain. Several HTTP/API monitor and detail families,
shared JSON-LD/DOM parsing, classification, experience, HTML normalization and
language detection also run through Go. Reuse those implementations.

Go parsing behind a Python caller is an intermediate state. Current Compose
still starts three ordinary workers and the browser worker with `uv run`, while the active Lightpanda overlay now selects the Go database executor for
the admitted cohort. Shared location/salary processing is also Go. Python still
owns ordinary orchestration and portions of scheduling, failure handling and
persistence. Porting another parser alone does not retire
these processes. See [Compose](../apps/crawler/docker-compose.yml),
[activation overlay](../apps/crawler/lightpanda-b0-enabled.override.yml),
[CPU stages](../apps/crawler/src/processing/cpu.py), and
[shared Go enrichment](../apps/crawler/go/job-enrichment/README.md).

The September 23 retirement census counted 7,854 enabled boards, 102 configured
monitor values, 34 non-null scraper types and 524 distinct boards requiring a
browser. Those are historical coverage denominators. Refresh the registry and
effective routes now and before final cutover; do not use those counts as a
current completion percentage. Resolve implicit scraper metadata, rich-monitor
detail skips, and enabled suspect/gone/quarantined rows explicitly.

## Delivery sequence

Each row is a milestone containing small PRs. The proposed order prioritizes
the saved work and the boundaries that keep Python processes alive. Profile
coverage and offline replay can continue while a production cohort waits for
naturally due work. Production owner changes remain serialized.

| Order | Deliverable | Exit evidence | Existing tracking |
| --- | --- | --- | --- |
| 1 | Resume and promote shared Go salary extraction | Refreshed current-head checks, complete deployment/promotion, natural Go calls, exact salary/rate/DB results and cold reversal | [#10177](https://github.com/colophon-group/jobseek/pull/10177), [#7952](https://github.com/colophon-group/jobseek/issues/7952) |
| 2 | Move remaining shared location resolution and CPU processing into reusable Go packages | Identical leaf IDs/type ordering, locale/remote semantics, cache misses/backfill, raw metadata and content/hash behavior on captured inputs | [#7952](https://github.com/colophon-group/jobseek/issues/7952) |
| 3 | Replace the Lightpanda database executor with a Go implementation for the admitted cohort | End-to-end render/extract/enrich/commit through Go; fenced stale results rejected; queue, failure, due-time and transaction effects conserved through crash/reversal | [#7951](https://github.com/colophon-group/jobseek/issues/7951), [#8648](https://github.com/colophon-group/jobseek/issues/8648) |
| 4 | Introduce a native ordinary Go worker for one already ported HTTP/API family, then expand by effective profile | Go owns claim, fetch, enrichment, persistence and reschedule; exclusive cohort selection; retry/streaming/TDM behavior and DB effects match | [#7951](https://github.com/colophon-group/jobseek/issues/7951), [#7954](https://github.com/colophon-group/jobseek/issues/7954), [#7955](https://github.com/colophon-group/jobseek/issues/7955) |
| 5 | Close enabled monitor/detail coverage and remaining browser classes | Every enabled board maps to a proven Go HTTP/API or Go + Lightpanda route; ordinary execution shows correct output/freshness without Chromium for the completed profiles | [#7956](https://github.com/colophon-group/jobseek/issues/7956), [#7957](https://github.com/colophon-group/jobseek/issues/7957), [#7962](https://github.com/colophon-group/jobseek/issues/7962), [#7963](https://github.com/colophon-group/jobseek/issues/7963), [#9980](https://github.com/colophon-group/jobseek/issues/9980) |
| 6 | Remove remaining scheduled Python runtime consumers and package the crawler service without Python | Deploy/startup/schema migration, repair, currency refresh, healthchecks, activation/reversal and recurring jobs have a proven replacement or explicit tooling boundary | [#7964](https://github.com/colophon-group/jobseek/issues/7964), [#7958](https://github.com/colophon-group/jobseek/issues/7958) where enabled document work requires it |
| 7 | Complete fleet resource measurement, quiesced cutover/reversal and retirement | Current enabled-fleet reconciliation, whole-lane parity/efficiency, zero production Python workers/Playwright/Chromium owners, then legacy removal after the rollback window | [#7935](https://github.com/colophon-group/jobseek/issues/7935), [#8648](https://github.com/colophon-group/jobseek/issues/8648); criteria in closed [#7966](https://github.com/colophon-group/jobseek/issues/7966) |

For milestones 3 and 4, move the existing state machine rather than inventing a
new queue/control plane. Preserve local Postgres truth, durable source identity,
write fences, CDC/writer-floor publication, R2 pending/hash semantics, deletion
and gone policy, retry/backoff and scheduler conservation. Include meaningful
operational fault tests: crash after commit before acknowledgement, stale
terminal after ownership retirement, duplicate delivery, cancellation, lease
recovery, and partial deploy/cold reversal. Honor existing connection budgets.

For milestone 5, prefer a proven upstream HTTP/API route where it provides
complete data. Admit Lightpanda capability classes separately: DOM navigation
first, then response capture and interaction/frame/session/proxy cases only
where current enabled profiles need them. Keep Chromium as an explicit
transitional owner until its replacement is proven. Kandou remains dark until
its required-field failure is resolved on the actual extraction input.
Do not disable an enabled board or silently substitute an empty result to
claim browser retirement.

## First resumption session

1. Read this plan and the salary handoff at the saved head. Fetch current main
   and PR #10177; inspect state, draft, head/base OIDs, changed files and checks.
   Preserve the existing candidate and other contributors' changes. Reuse its
   branch in an isolated checkout or create an isolated checkout of that exact
   branch. Do not recreate salary extraction or edit the primary checkout.
2. Account for main drift and VERSION allocation. Rebase/update only where
   needed, then run affected checks and installed-image parity on the new head.
   The original green checks do not transfer to a changed candidate.
3. Record live committed release/image identities, active cohort receipt,
   owner/epoch, queue membership, selectors, mutation lock and service health.
   Recheck deployment holds. This plan authorizes no production promotion by
   itself; use the continuing session's operator authorization. The deliberate
   draft state must not be overridden merely to make a status green.
4. During an authorized deployment window, follow the supported cold procedure:
   rollback the current active cohort (recorded as cdom), clear the exact
   selectors at the full current promoted revision under the mutation lock,
   refresh merge authority and merge bound to the tested head, wait for terminal
   deployment success including promotion, stage selectors at the new full
   promoted revision, then activate through the supported wrapper.
5. Observe normal salary executions and canonical DB readbacks on the same
   stored bytes and supplied currency rates. Capture failures, scheduling,
   content hashes and scalar/R2 convergence. Save a deployed checkpoint or a
   precise blocked candidate checkpoint, then choose the next location slice.

Use [ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md) and the current
[resumption operational record](24-go-lightpanda-resumption-plan.md). The
recorded selector helper is `scripts/migration-jsonld-selectors.py`, SHA-256
`8232a219cc9bc4236a9aab6d77ee85321f257afd7d577c365c7b3ba636ac9743`;
the recorded Kandou URL is `https://kandou.bamboohr.com/careers/310`.
Validate the helper and live revision before use; copy no historical activation
command without that refresh. Let bounded reconciliation finish if it holds
the lock. Do not bypass locks, edit live env files, force due/priority, restart
only part of the writer set or create duplicate publisher traffic.

## Pace and checkpoint discipline

Proposed pace: one bounded implementation slice per working session and one
production promotion at a time. Aim to promote a prepared slice within the next
one or two active sessions when required checks and operational evidence allow.
This is a planning target, not a deadline that overrides correctness. No
scheduled automation is created by this plan.

Maintain at most one candidate awaiting production promotion and one independent
offline implementation slice. A session should end with a reviewable PR and
verified evidence, a deployed milestone, or a concrete blocker with a named
next action. A checkpoint is a handoff, not proof that the full migration is done.

Run focused behavioral checks and the required CI/contracts once on the final
candidate. Repeat only after a change, failure or specific unresolved question.
Do not add another optional verification cycle after the evidence suffices.
While waiting for natural due work or a bounded maintenance job, proceed with
independent offline work; do not poll unchanged queues or generate extra origin
requests to produce evidence.

Track coverage by effective execution profile and production process owner,
with board IDs/selectors, route, capability class, parity evidence, deployed
revision, rollback path and remaining gap. Rank the next family using current
enabled/due volume, operational pain and ability to reuse Go code. Implementation
of unused families is not required for crawler service retirement.

After each promotion, update one concise current section in this plan, preserve
history in the resumption log, and link sanitized evidence. Reconcile the epic's
dated status when publishing the checkpoint under explicit issue-write
authorization. Keep the existing issue numbers instead of opening a duplicate
migration program. A portable checkpoint must contain:

- Full candidate and promoted revisions, immutable image/renderer identities,
  version, PR state and exact-head check results.
- Live owner/epoch and selector snapshot date, enabled-profile coverage delta,
  natural execution/output/failure evidence, and tested cold reversal.
- Committed sanitized evidence and reproducible input hashes; protected raw
  content stays outside Git. Local paths are optional aids, never the only
  restart record.
- One next slice, concrete blockers, and what evidence clears each blocker.

## Resource evidence and completion gates

Measure resources as each native boundary replaces a bridge, then repeat on the
complete migrated lane. The recorded 20x512 language-stage replay used
1.882/1.912 seconds Python CPU and 3.255/3.288 seconds Go CPU including bridge
and child. That stage was slower on those inputs. Parser parity and synthetic
density do not establish whole-lane production savings. Consolidating native
worker/enrichment execution is a proposed way to remove IPC overhead; measure
whether it helps.

Use single captured upstream inputs for offline comparisons and normal live
work for correctness/freshness. Compare the same workload, successful output,
concurrency, due window and publisher policy, including all Python bridges,
Go processes, browser/renderer, idle time and attributable support work. Report
CPU, sampled/peak/retained memory with measurement limits, correct completions
per resource unit, freshness, retries/errors/OOMs and attributable operating
cost. Missing observations and unequal input windows remain explicit gaps.
Use existing [runtime cost contracts](../apps/crawler/runtime-cost/) where
applicable; do not force the superseded projection into an actual-load decision.

Final acceptance requires current enabled-fleet coverage, canonical output and
intended DB effects, publisher-policy parity, conservation through cutover and
cold reversal, and better whole-lane efficiency without material throughput or
freshness regression. Agree and record the rollback observation window against
the actual schedules before deleting legacy assets; there is no arbitrary
seven-cycle or fixed-URL quota. Remove old code/images/credentials only after
the replacement is authoritative and that window has passed.

## Reasonable Python boundary

The target is zero Python execution required by the deployed crawler service:
workers, persistence, enrichment, browser execution, scheduled runtime
maintenance, startup/migrations, healthchecks and supported cutover/reversal.
Audit deploy scripts and timers as well as container commands. Merely changing
the worker executable or deleting a dependency from one image is insufficient.

Python can remain in separately packaged developer tooling, frozen test oracles,
one-off historical migrations and offline dataset/labeller/workspace workflows
where a rewrite adds little value. An operational exception must name its
consumer, whether it touches live state, why it remains, and its isolation and
removal criteria. A live mandatory Python step means service retirement is
incomplete even if ordinary crawling is already native Go.

Keep comparison code and rollback artifacts outside the lean production image
once reversal can use a complete prior digest-pinned release. Remove Playwright,
Chromium and legacy runtime-only dependencies only after every enabled browser
profile has a proven replacement. If a capability remains unresolved, report
the remaining owner and profile explicitly and continue useful migration work.

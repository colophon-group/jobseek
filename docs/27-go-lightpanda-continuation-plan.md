# Go and Lightpanda migration continuation plan

## Delivery objective — revised 2026-10-03

Deliver the migration in full: production Go workers and self-hosted Lightpanda
or proven Go HTTP/API routes must replace every enabled crawler profile, then
remove production Python, Playwright and Chromium after the supported rollback
window. Preserve useful isolated offline Python tools and every enabled board.

Judge progress by deployed native owners, enabled profiles migrated and mandatory
production Python consumers removed. Check canonical/database/publisher behavior,
freshness and queue conservation on real jobs, and compare whole-service resources
and cost. Add a fixture or abstraction only for a specific changed production
contract or observed failure. Plans and synthetic evidence are supporting work.

All 2,570 enabled Greenhouse boards are now owned by the Go ordinary worker
on v0.13.910, source `c7b1dcf2c4d25a2677aaf70073928a9e940fd441`, at epoch 151.
[Deployment 37137484149](https://github.com/colophon-group/jobseek/actions/runs/37137484149)
completed promotion, and the installed supported activation driver restored the
complete stack. All eight HTTP readiness endpoints passed. The 20:40 UTC readback
verified all 2,570 SQL and Redis members and deadline agreement for 51 completed
current-epoch receipts. Sequential metrics recorded 50 successes, 1,461 posting
touches and zero claim, transport, execution or cancellation errors. New posting,
description/enrichment and disappearance transitions still need natural evidence.
See the [latest checkpoint](30-native-ordinary-worker-checkpoint-2026-10-03.md).

That early observation is historical. At 21:47 UTC the same release had 547
successful tasks, ten failed tasks, nine timeouts, twenty unacknowledged tasks,
21 claim errors and 155 host-circuit refusals. The 21:29 all-member check found
534 completed deadlines matching and three completed SQL receipts with earlier
Redis retry deadlines; its strict parity check failed. Do not expand ownership
until a fresh observation resolves those discrepancies. The bounded worker fix
in [PR #10249](https://github.com/colophon-group/jobseek/pull/10249) removes repeated
decoding of the immutable fleet ownership document, keeps fresh active-identity
and canonical/cache checks, and adds safe processing-phase diagnostics. It does
not change pool sizes, timeouts, retry policy or claim authority, and its
production failure-resolution claim remains unproven.

[PR #10246](https://github.com/colophon-group/jobseek/pull/10246) is merged at
`bcf7811e0575d397b58f45ce206977a851578870` with native Ashby/Lever rich execution;
its 16-arm ARM64 admission passed. The candidate census admitted 1,128 skip
profiles, with two detail profiles excluded. [PR #10248](https://github.com/colophon-group/jobseek/pull/10248)
is merged at `38447db97467eb73a1a5db19005193e618fe4b05` and replaces four Compose
Python health probes with the installed Go executable. Neither slice is deployed
at this checkpoint: the original active-ownership guard refused both automatic
deployments while v0.13.910 continued serving. Build the approved combined
release, retire ordinary ownership with its original driver, roll back B0 and
clear its exact selectors, then use the complete supported deployment and
reactivation. Verify native task outcomes and deadline parity before adopting
fresh Ashby/Lever profiles.

The next code slice connects the existing Recruitee and Pinpoint parsers to the
same native rich claim, fetch, enrichment, persistence and settlement path.
The 21:46 UTC canonical/cache capture admits 218 skip profiles out of 219;
Floryn's detail profile remains outside this slice. Thirteen actual Python
request fixtures bind tenant/API-base precedence. Real PostgreSQL/Redis tests
verify rich fields, description storage/upload scheduling, matching deadlines,
Recruitee provider-gone 404s, Pinpoint ordinary 404 failures and publisher
reservation without partial writes. These are candidate results; production
native owners for these providers remain zero. Publish this slice after the
worker fix, then refresh its census against the approved release.

Continue with Ashby and Lever through the existing rich worker and queue
contracts, then every remaining provider/browser profile and mandatory Python
runtime consumer. Keep every enabled board scheduled. Prove full-service output,
publisher policy, freshness, queue conservation and resources, exercise supported
reversal and observe its rollback window before removing production Python and
legacy browser assets. The full delivery goal remains active until that retirement
is complete. Avoid additional frameworks or fixture expansion unless a changed
production contract or an observed failure requires them.

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

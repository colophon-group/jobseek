# Go and Lightpanda migration continuation plan

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
Linux AMD64/ARM64 Redis contracts passed. It now also prepares native
PostgreSQL attempt authority, guarded reaping and canonical deadline recovery.
At head `86813918a`, Required CI, installed runtime contracts, both Linux
PostgreSQL/Redis architectures and all 16 synthetic B0 admission arms passed.
Stacked draft [PR #10210](https://github.com/colophon-group/jobseek/pull/10210)
adds strict Greenhouse eligibility and canonical PostgreSQL/Redis observation;
implementation head `e5ecd5d29` passed both real Linux database/queue jobs.
Its subsequent `6a71c4c99` ownership implementation adds immutable durable
staged/active/retired cohorts and exact active readback; both Linux PostgreSQL17/
Redis architectures passed run 36790040524. Subsequent `f7ccc7b06` binds native
and legacy claims to the exact active plan, canonical configuration and atomic
before-pop selection; both Linux architectures passed run 36793700617 with
mandatory real legacy PostgreSQL/Redis attestation tests. Rich processing source
`8a5834e61` now adds native content preparation against 18 captured Python cases
and owned atomic 500-posting insert/touch/relist/description batches; both Linux
architectures passed run 36796933484. Follow-up `449e58052` rechecks the current
board publisher reservation under the canonical write lock and passed local
real database/queue races. Checkpoint `01b7366f5` subsequently passed both Linux
architectures in run 36797683763 and full CI in run 36797708877; its B0 admission
run 36797683889 was still measuring when the next implementation was saved.
Those results do not cover later source or grant merge authority for a draft.

Latest implementation `8a6f1bc08` binds owned Greenhouse batches to a terminal
board cycle. It applies disappearance/empty/partial/confirmed-contraction,
quarantine/backoff, provider-404 and publisher-reservation policies under the
same PostgreSQL attempt authority and commits the canonical due time with the
receipt. Enabled suspect/quarantined/gone_pending/gone states retain their
installed native owner; fresh PostgreSQL metadata avoids stale Redis baseline
and confirmation state. Local real database/queue proof includes 41 actual
Python decision cases, 10 lifecycle integration groups and commit-before-ack
recovery in changed board states. See the
[portable lifecycle evidence](evidence/go-ordinary-lifecycle-2026-10-01.json).
Checkpoint `7e1094733` passed both Linux architectures in run 36800629362,
full CI in run 36800624392 and B0 admission in run 36800629213. The actual
deploy gate still rejects the draft; earlier-head greens grant no later-head
merge authority. Production ordinary workers remain Python.

Latest processing assembly `9d7a8f5a8` adds the
[native ordinary package](../apps/crawler/go/ordinary-worker/README.md).
Forty URL and six full-inventory captures match the actual default Python rich
monitor's dictionary, sanity/canonicalization and alias content rules, including
interleaved duplicates. Native normalization keeps every collected job above
the 50,000-job flag. A real owned PostgreSQL/Redis fixture now connects native
models, lookup/currency snapshots, SQLite locations, three 500/500/1 posting
batches, terminal disappearance and canonical receipt settlement for 1,001
postings. Preparation failure after a committed prefix invalidates later
success/absence authority. Current source remains unselected and needs fresh
checks; see [portable pipeline evidence](evidence/go-ordinary-pipeline-2026-10-01.json).
See the
[ordinary authority checkpoint](30-native-ordinary-authority-checkpoint-2026-09-30.md).
It selects no ordinary worker in production. Next, connect the completed full
inventory/preparation/batch/cycle assembly to the native executable. Complete shared HTTP transport,
retry/circuit/deferral behavior, startup/shutdown/heartbeat and typed provider/
publisher response handling. Preserve the full Redis claim snapshot through
settlement and recovery; terminal lifecycle metadata is read from PostgreSQL.
Then prove the installed
executable and exclusive ownership with fault/cold-reversal evidence, followed by
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

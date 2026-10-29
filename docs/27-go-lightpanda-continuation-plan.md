# Go and Lightpanda migration delivery plan

## Current checkpoint — October 30, 2026

The **0.13.1123** full deployment
[40966476322](https://github.com/colophon-group/jobseek/actions/runs/40966476322)
succeeded, including promotion. Production source is
`6732ad7e3f4362f6c4c701377d8c162f352fd7c3`, at default epoch 404, using
`ghcr.io/colophon-group/jobseek-crawler@sha256:b9b7ca17d40c077389d74eb7fa18e2b457b2cb613bf55ced1ad269dde4afd8a7`.
The browser repository references the same native image digest. Independent
readback verifies the source/image/runtime/data contracts, schema 0041,
8,143 enabled boards, four running native actors and seven stopped legacy actors.
Deployment timestamps below are UTC.

The missing-schedule correction completed both inspection and apply across
**2,050,912 eligible canonical rows**, reconstructing **16 absent projections**.
One read-only forward cursor replaces the expensive globally UUID-ordered pages,
while retaining bounded FETCH batches, the five-million-candidate limit, one
connection, eligibility, ownership checks and the original 15-minute deadline.
Existing lifecycle, fallback, retry and pacing state remains preserved.

The installed-source SDK passed **8,143 monitor configurations, 8,143 detail
configurations and 2,052,231 scheduled detail URLs**, with zero unqueued snapshot
gaps or pending lane normalizations. The complete monitor census has no wrong,
unknown or malformed routes. The complete detail census has no missing caches
or wrong domains; retained wrong-worker entries still require canonical lifecycle
handling. All five apparent monitor gaps resolve to four current queue/inflight
members and one dead letter.

The current complete queue boundary contains **3,491,254 identities**. All
**740** removals are accounted for: 652 inactive/disabled, 68 unscheduled,
19 present in current queues/inflight, and one dead letter. There are no missing
canonical records or unexplained active scheduled removals. Consumed bulk
captures and the superseded boundary have been deleted.

Stability is **not accepted**: the native worker restarted once with
`transport_selection:configuration`. A bounded fatal-window replay validates
21 other claims and isolates an inactive, unscheduled detail whose canonical
board is disabled and whose board cache was removed. Default activation still
granted this stale member a productive SQL fence before transport selection
refused its missing profile.

The **0.13.1124** correction checks current board eligibility under the existing
canonical board lock **before activating or replacing a default-mode fence**.
Disabled or nonrecoverable boards return nonfatal authority loss for both monitor
and detail claims in either worker lane. Their exact raw lease remains available
to the original bounded reaper/dead-letter policy; no content, canonical deadline,
existing fence or failure budget is rewritten. Enabled active, suspect,
quarantined, gone-pending and gone boards retain their existing execution path.
Active ownership plans keep their current behavior. Real PostgreSQL/Redis tests
cover both kinds/lanes, inactive and still-scheduled disabled details, removed
board caches, old fences, refusal of writes, original final-retry dead lettering,
and every recoverable enabled board state.

The full migration goal remains active. Qualify and deploy this correction
through required exact-head CI, fresh merge authority and the original complete
workflow. Use **self-hosted Lightpanda or proven Go HTTP/API routes**, preserve
every enabled board, and retain the existing runtime limits and rollback guards.
No manual SQL/Redis/environment/epoch repair or partial restart is authorized.

## Latest Lightpanda

The installed renderer is **1.1.0-nightly.11275+78bbb64db**, upstream commit
`78bbb64dbe1a3e1df8d2299cbf05efcff81bd65f`, matching the latest official
October 29 Linux assets. Its immutable image is
`ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:4361f406e77fc0bd2fab56533e0bab51b56773e2c398cdbd78fd1e7b7c707805`.
Retain the reviewed 1 GiB memory, zero swap, concurrency, child heap and response
limits. Check upstream again when continuing on a later release day.

## Schedule and retired-detail correction — 0.13.1121

Fifteen active, enabled, scheduled details on one former B0 sitemap board lack
both their cache and queue projection. Fresh canonical inspection confirms
existing descriptions, unchanged deadlines and no live SQL leases. All fifteen
retain the existing browser route. They were outside the previous ordinary-only
verification cohort. A sixteenth non-atomic snapshot gap is now unscheduled.

The same batch corrects default-mode retirement of stale unscheduled detail
attempts. Once fresh canonical SQL says the posting is inactive or unscheduled,
resolve its validated canonical worker profile for the existing no-fetch
retirement path. Keep the original claim's worker, epoch, token, cached snapshot
and canonical identity guards through receipt settlement. Scheduled work still
requires the exact claimed route. A rescheduling race or changed authority
refuses retirement; content and failure budgets remain unchanged. Real
PostgreSQL/Redis worker tests cover Workday and API sniffer with zero HTTP or
Lightpanda calls, including reactivation and token/configuration refusal.

The original canonical rebuild now has a bounded `repair-missing` action. It
retains the existing scraper eligibility and additionally excludes disabled,
reserved, unscheduled, inactive and currently leased records. It streams one
read-only canonical snapshot in bounded batches, then inspects only absent
caches. Existing caches, fallback steps,
queues in either lane, attempts, tokens, dead letters and retry counts are
preserved. Restore a wholly absent projection through the original enqueue
script using its canonical URL, description hash, deadline, interval and lane.
Existing rate, rotation and delay state is retained; repeated apply is a no-op.

The original full deployment inspects and applies this recovery after registry
sync and default epoch preparation, before restarting processors. It mounts the
existing producer authority volume read-only and requires default SQL/Redis
ownership and dormant producer authority. Inspection is physically read-only;
any inspection or apply failure stops the transaction through its existing full
rollback. No manual repair command, forced fetch, timer change or resource
increase is part of this rollout.

Local qualification includes real PostgreSQL/Redis retirement, canonical paging and
eligibility, the actual numeric description-hash column type, installed CLI dry
run/apply/repeat, atomic refusal and preserved lifecycle state. An isolated replay
of all fifteen actual configurations qualifies their current Go/Lightpanda
profiles and preserves exact canonical fields and deadlines without network
fetches. The exporter module tests, vet, module checks and 92 deployment contract
tests pass. Required CI, exact-source full deployment and independent production
readback remain necessary.

## Reversal and native runtime

The original complete Go-to-legacy cold reversal succeeded in workflow
[40899557014](https://github.com/colophon-group/jobseek/actions/runs/40899557014).
Independent readback verified complete prior actor, image, specification and data
restoration. The allocator was retained; later Go releases retain epoch 404.

Use the original full workflow and
[ADR 006](adr/006-crawler-deploy-quiescence-and-rollback.md): immutable identities,
mutation locks, full writer quiescence and authenticated generation/spec
restoration. Preserve the active generation, durable journal references, explicit
rollback target, five newest verified v3 generations and 48-hour staging grace.
A failed restore keeps writers stopped. Do not use partial restarts, manual
SQL/Redis/environment/epoch repair, mutable tags or live rsync.

The native image provides Go workers, exporter, description drain, lease recovery,
registry/migration/maintenance, inventory, conversion/OCR and enrichment support.
The four processing services are `ordinary-go`, `lease-reaper`, `exporter` and
`drain`. Useful isolated Python company setup, labelling, research, tests and
operator tooling may remain; production processing uses the native image.

## Finish the production exit checks

1. Complete 1124 required checks and its exact-source original full deployment.
   Independently bind source/image/data/default mode/epoch, healthy native actors,
   stopped legacy processors and all fifteen restored schedules.
2. Repeat the complete installed-source SDK for the final source and actual
   current fleet. The 1123 run qualified all 2,052,231 scheduled URLs and all
   enabled configurations; retain its result without treating the restart as
   accepted stability.
3. Compare complete unfiltered queue/inflight identities against the current
   3,491,254-identity boundary and its monitor frontier. The older detail-only
   boundary is consumed. Verify current dispositions for every new removal;
   conservation does not itself establish correct scheduling or freshness.
4. Verify sustained natural monitor/detail completions, canonical output,
   enrichment, exporter/drain progress, publisher/failure behavior and freshness
   with fixed process identities and no restarts. Preserve inventory for reserved
   or unavailable publishers. Do not force jobs or clear counters/circuits.
5. Complete aligned crawler, renderer, PostgreSQL and Typesense resource windows.
   Earlier stability/resource windows were rejected; repeat after the
   eligibility correction with fixed process identities and no restarts. The qualified legacy control
   recorded 726 monitor and 1,454 detail successes in 566.8 seconds. The earlier
   unstable 1118 comparison used less CPU/RAM but delivered less throughput and
   modeled higher cost per useful task; no cost or throughput win was accepted.
   Report task mix, shared storage work, allocation assumptions and unchanged
   provisioned charges. Resource-share models are not billed savings.
6. Establish the supported retained recovery window and retire obsolete
   production-only legacy execution/assets where reasonable. Update this plan
   with accepted installed identities, results, limitations and remaining work.
   Mark the goal complete only after required production outcomes are verified.

## Storage and continuation

Keep one current checkpoint, one current conservation boundary, current
qualification and operational rollback inputs. Delete superseded local captures,
archives, copied scripts, test databases and compiled test binaries after each
completed batch. Historical evidence need not be preserved. Do not remove other
contributors' work or change production generation retention.

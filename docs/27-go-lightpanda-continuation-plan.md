# Go and Lightpanda migration delivery plan

## Current checkpoint — October 29, 2026

The **0.13.1121** full deployment
[40942821136](https://github.com/colophon-group/jobseek/actions/runs/40942821136)
failed at **20:19:54 UTC**: its missing-schedule inspection reached the unchanged
15-minute command deadline. The rollback then refused the browser lease sweep
and left processors stopped. Promotion was skipped. The retained verified
release is 1120, source `45b2c700f96ef59101ab4789f139510ed864a014`, at epoch 404;
its selected environment is not evidence that processing has resumed.

The **0.13.1122** correction batches two causes. PostgreSQL's generic plan for
the nullable cursor filter has no posting primary-key index bound and repeatedly
scans earlier rows. Subsequent canonical pages now use a separate non-null UUID
seek query, retaining eligibility, row bounds and the existing command deadline.
Read-only production EXPLAIN verifies an index condition for the proposed query;
the private PostgreSQL regression checks the generic plan as well as paging.

Three current-epoch, exact-token completed monitor receipts retain old Redis
domain keys. Their current configurations, stable fingerprints and canonical
deadlines validate. Default-mode recovery now carries the independently validated
canonical domain separately from the expired source member, including when the
worker lane also changed. Atomic Lua validates both routes before any effect,
retires only the exact old token/member, and preserves a newer target schedule,
attempt, dead letter and runtime cache. Active ownership plans retain their
original domain restrictions. Registry publication also inventories all four
monitor queue families and retires queued old-domain aliases only after the
canonical selected schedule exists and the old route has no live lease/token.
This covers unfinished attempts that expired before sync, while preserving
unrelated source-domain work and rate/rotation floors. Tests cover same-lane and cross-lane recovery,
repeated application, and token/configuration/deadline/ownership/index refusal.

The full migration goal remains active. Resume through a qualified exact-source
original full deployment, with writer quiescence and complete authenticated
rollback. No manual SQL, queue, environment, epoch or partial restart repair is
authorized. Use **self-hosted Lightpanda or proven Go HTTP/API routes**, preserve
every enabled board, and retain existing runtime resource limits.

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
reserved, unscheduled, inactive and currently leased records. It seeks canonical
UUID pages, then inspects only absent caches. Existing caches, fallback steps,
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

1. Complete 1122 required checks and its exact-source original full deployment.
   Independently bind source/image/data/default mode/epoch, healthy native actors,
   stopped legacy processors and all fifteen restored schedules.
2. Run the complete installed-source SDK across every enabled configuration and
   scheduled active detail URL. The previous full run checked 2,038,879 routes
   with no cache identity mismatch, but refused the fourteen monitor lanes.
   That historical run is not final acceptance; rerun after the remaining repair.
3. Compare complete unfiltered queue/inflight identities with the retained current
   boundary of 3,465,138 identities. All 290 previous removals are accounted for:
   275 inactive/disabled and 15 unscheduled. Verify new dispositions and explain
   every removal. Conservation does not by itself resolve missing projections.
4. Verify sustained natural monitor/detail completions, canonical output,
   enrichment, exporter/drain progress, publisher/failure behavior and freshness
   with fixed process identities and no restarts. Preserve inventory for reserved
   or unavailable publishers. Do not force jobs or clear counters/circuits.
5. Complete aligned crawler, renderer, PostgreSQL and Typesense resource windows.
   The first 1120 stability/resource window was rejected; repeat after the
   retirement correction. The qualified legacy control
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

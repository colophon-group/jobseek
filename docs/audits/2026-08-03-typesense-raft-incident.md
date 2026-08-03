# Typesense Raft and descriptor incident — 2026-08-03

## Status and impact

Production Typesense was process-up but API-unready from 2026-07-25 16:38 UTC
until 2026-08-03 10:15 UTC. Search, typeahead, browse/filter modals, watchlist
search, company-detail reads, exporter writes, and reconciliation were degraded
or unavailable. Docker reported a continuously running container with zero
restarts, so process-only monitoring was falsely green.

The node was recovered without resetting Raft peers or restoring stale data.
It loaded all seven collections from the last local Raft snapshot, replayed the
remaining log, elected itself leader in term 3, and returned healthy after a
9-minute cold start. Public and private health, all aliases, representative
searches, and an ephemeral create/write/read/delete probe passed.

## Causal chain

Evidence is preserved root-only on the Typesense host under
`/var/lib/jobseek-incidents/6119/20260803T100106Z`.

1. At 16:21:01 UTC the 16-thread request pool began reporting exhaustion.
   There were 5,364 exhaustion messages and the queue reached 940 tasks.
2. At 16:21:47 UTC Typesense began reporting slow requests. There were 3,036;
   the slowest completed after 312,999 ms. The affected traffic consisted of
   broad posting searches and count/facet queries, not a storage operation.
3. At 16:22:31 UTC the process reached its inherited soft `nofile` limit of
   1,024. It logged 964 `Too many open files` failures through 16:38:43.
4. At 16:38:40 UTC the hourly Raft snapshot could not create its
   `SnapshotWriter`. The node stepped down and entered persistent Raft
   `ERROR` with no leader.
5. The process never exited, and the single-node group could not recover while
   its in-memory Raft node remained in `ERROR`. This amplified 150,714
   leaderless/error lines through the response on August 3.

The root cause was unbounded request saturation combined with an unmanaged
1,024 soft file-descriptor limit. Disk capacity, OOM, and container restart
failure were ruled out. The failed snapshot was the transition from severe
request degradation to a persistent leaderless outage.

## Recovery and rollback evidence

Before restarting, the responder stopped Typesense and copied the complete
1,918,920,698-byte data directory to the incident directory. Source and copy
byte counts and file counts matched (109 files). The failed container was
renamed and retained as `typesense-incident-6119-pre-recovery` so its exact
metadata remains available.

The least-invasive repair was used first: start the same 27.1 image and data
with a 65,536 soft/hard `nofile` limit. Typesense rebuilt its in-memory index,
loaded 2,505,885 `job_posting` documents, replayed 579 log entries, and elected
itself leader. The supported `--reset-peers-on-error` last-resort option was
not used because Typesense warns that it can cause intermittent data loss.

Acceptance at recovery time:

- loopback, crawler-private-network, and public tunnel `/health`: HTTP 200,
  `ok:true`;
- `/debug`: Raft state `1` (leader), zero recurring `state ERROR` messages;
- aliases: `company`, `job_posting`, `location`, `occupation`, `seniority`,
  `technology`, and `watchlist`, each resolving to its versioned collection;
- representative posting/company/watchlist searches returned results;
- an isolated collection create, document upsert/read, and exact collection
  delete passed;
- live process `nofile`: 65,536/65,536; 47 descriptors open; zero restarts.

Rollback remains: stop the recovered container, restore the offline copy to an
empty data directory, and start the preserved container metadata. Do not copy
the active data directory while Typesense is live.

## Capacity and topology

At recovery, the host had 2 vCPUs, 4.0 GB RAM, and a 40 GB root filesystem.
The Typesense data directory was 1.8 GB. After loading 2.5 million postings,
Typesense RSS was 1.88 GB and the host had 1.50 GB memory available. The root
filesystem had 14.6 GB available after retaining the additional offline copy.
The incident was saturation, not current memory or disk exhaustion.

The present single node cannot tolerate a host or process failure. Typesense
[recommends a 3- or 5-node production topology](https://typesense.org/docs/guide/system-requirements.html#choosing-number-of-nodes),
and a single-node restart necessarily remains a cold-start outage. Moving to
three nodes is the durable availability option; until that is approved, the
recovery-time objective is bounded by the measured 9-minute reload plus
operator response. PostgreSQL remains the rebuild source of truth.

## Permanent controls

- Manage and verify a 65,536 soft/hard Docker `nofile` limit.
- Rotate Docker JSON logs at 50 MB with three files so a persistent Raft state
  cannot consume unbounded root-disk capacity.
- Allow 15 minutes for the measured cold-start reload before deploy rollback.
- Export live descriptor use/limits, process threads, five-minute maximum
  thread-pool queue depth, slow-request duration, and exact descriptor,
  snapshot, and leaderless log events.
- Alert independently on readiness, unsafe limits, 75% descriptor use,
  request-pool saturation, slow requests, descriptor exhaustion, snapshot
  failure, and leaderlessness.
- Keep peer reset as a documented last resort only after an offline copy.
- Restore and verify the application-consistent backup in the isolated backup
  follow-up, issue #6120, before deleting the incident copy.

Typesense 27.1's config parser requires a `[server]` INI section even though
the versioned documentation describes only the parameter names. The managed
host config and its real-container smoke test include that section; omitting it
causes a crash loop with `Data directory is not specified`.

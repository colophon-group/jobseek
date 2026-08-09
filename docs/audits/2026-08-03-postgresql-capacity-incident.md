# PostgreSQL Capacity Incident — 2026-08-03

Tracking issue: [#6117](https://github.com/colophon-group/jobseek/issues/6117)

All times below are UTC. Host addresses, provider resource identifiers,
repository credentials, cipher material, and row contents are intentionally
omitted.

## Timeline

- 2026-08-01 01:07 — the last pre-incident differential backup completed.
- 2026-08-01 09:19 — the encrypted backup repository reached capacity and
  pgBackRest `archive-push` began failing with `ENOSPC`.
- 2026-08-01 15:59 — retained unarchived WAL filled the 40 GiB XFS data Volume;
  PostgreSQL entered a crash-recovery loop.
- 2026-08-02 01:01 — the scheduled backup failed because PostgreSQL was
  restarting. The wrapper did not run repository expiration independently.
- 2026-08-03 01:06 — the next backup failed for the same reason.
- 2026-08-03 08:24 — the audit opened #6117 after dependent crawler,
  maintenance, deployment, backup, and host-probe paths had failed.
- 2026-08-03 08:34 — incident response preserved root-only filesystem,
  container, WAL, backup, and journal evidence and stopped the futile restart
  loop after 2,397 restarts.
- 2026-08-03 08:37 — pgBackRest inventory proved that the repository retained
  every continuous WAL segment since the first full backup. A dry run selected
  obsolete WAL only and no backup set.
- 2026-08-03 08:38 — emergency pgBackRest-managed archive expiration began
  under the backup lock.
- 2026-08-03 09:00 — the protected data Volume was expanded from 40 to 80 GiB
  and XFS was grown online. PostgreSQL completed crash recovery, passed
  read/write probes, and an allocated 2 GiB emergency reserve was established.
- 2026-08-03 09:01 — the archive sentinel was held while the repository
  expiration owned the slow remote filesystem. PostgreSQL remained ready with
  no further container restart.
- 2026-08-03 09:18 — all seven automatic Storage Box snapshots that predated
  expiration were removed. They held 128 GB of already-expired WAL blocks;
  repository free space immediately recovered while pgBackRest continued its
  metadata-safe expiration.
- 2026-08-03 11:25 — pgBackRest expiration completed successfully. The
  repository was 13% used with approximately 962 GB free.
- 2026-08-03 11:37–11:56 — WAL archiving resumed and drained all 1,538 queued
  segments without increasing the archive failure counter. A checkpoint then
  reduced the data Volume from 58% to 31% used and restored approximately
  56 GB of ordinary free space.
- 2026-08-03 12:01 — a new full backup `20260803-115836F` completed in 235
  seconds and pgBackRest reported the repository healthy.
- 2026-08-03 12:02 — PostgreSQL completed one guarded restart into the
  repository-lock-aware archive command. Private-network read/write, WAL
  archive, listener, shared-memory, restart-count, and public-ingress checks
  passed before the rollback transaction was committed.
- 2026-08-03 12:23 — the fresh full backup completed an isolated restore and
  exhaustive heap/B-tree relation check with no errors. The disposable
  container and 19.1 GB restore directory were removed.
- 2026-08-03 12:28 — the Storage Box automatic plan was re-enabled for one
  daily 06:00 UTC snapshot with a hard maximum of two retained snapshots.
- 2026-08-03 12:37 — the crawler-side operational preflight passed all eight
  readiness, telemetry, filesystem, reserve, backup, and archive-health gates.
- 2026-08-03 12:48 — the three HTTP workers, browser worker, exporter, and R2
  drain resumed without recreating their containers. All worker/browser health
  checks passed with zero restarts or OOM kills.

## Root cause

The repository was configured with `repo1-retention-full=4` and no independent
archive retention. pgBackRest therefore retained continuous WAL for four
weekly full backup chains. Only two full backups existed when more than 1 TB
of WAL filled the repository, so normal expiration had never become eligible.
The backup wrapper also checked the live PostgreSQL container before any
repository maintenance, making the full-repository/database-down combination
self-sustaining.

The durable database measured 19.79 GB, up by less than 0.6 GB from the 19.2 GB
baseline on 2026-07-22. That is approximately 1.5 GB of linear 30-day growth.
Relation growth, XFS inode exhaustion, PostgreSQL bloat, and the configured
4 GiB WAL checkpoint ceiling were not the capacity source. After archive
catch-up, the 80 GiB Volume leaves more than 50 GB of ordinary headroom after
the allocated 2 GiB reserve, well beyond that measured forecast.

## Corrective controls

- retain four full and seven differential backup points, but continuous WAL
  for only the two latest differentials;
- expire obsolete WAL in a networkless one-shot before checking PostgreSQL
  health, and pass the exact retention contract to every backup;
- maintain an allocated 2 GiB XFS emergency recovery reserve;
- alert at 35% repository free or a projected seven-day exhaustion;
- block crawler deploy and scheduled maintenance before workload mutation when
  PostgreSQL readiness, backup/archive health, either filesystem, telemetry,
  or the emergency reserve is unsafe; and
- preserve fresh backup and isolated restore proof after recovery.

Production acceptance evidence is appended to #6117 after every verification
gate passes.

## Production acceptance

- The 80 GiB XFS data Volume is 31% used with approximately 56 GB free, plus
  the root-owned, fully allocated 2 GiB emergency reserve.
- The encrypted 1 TB repository is 13% used with approximately 965 GB free.
  Retention is exactly four full backups, seven differential backups, and WAL
  for the two latest differential sets.
- The fresh full backup represents 20.54 GB of database data and 9.12 GB of
  repository data. The isolated restore verified PostgreSQL 16 recovery,
  `archive_mode=off`, every heap and B-tree relation, 26 public tables,
  5,061 companies, 5,740 job boards, 2,592,398 postings, and zero structurally
  invalid postings.
- PostgreSQL is ready with zero restarts after the guarded replacement. It
  listens only on loopback and the Hetzner private address; the crawler host
  completed a transactional read/write probe and the public endpoint is
  unreachable.
- The live Mimir rules for data-Volume headroom, backup-repository headroom,
  and emergency-reserve allocation are healthy and inactive against current
  values. Their selectors observe 69.95% XFS free, 87.8% repository free, and
  the exact 2 GiB reserve.
- Backup, emergency-headroom, Alloy, and host-sampler timers/services are
  enabled and active. Root-only machine-readable evidence is retained under
  `/var/lib/jobseek-incidents/6117/20260803T083440Z`.
- The crawler operational preflight passed immediately before workload resume.
  Resumed services remained healthy while WAL archiving continued without any
  increase beyond the incident failure baseline.

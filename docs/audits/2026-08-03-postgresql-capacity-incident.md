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

## Root cause

The repository was configured with `repo1-retention-full=4` and no independent
archive retention. pgBackRest therefore retained continuous WAL for four
weekly full backup chains. Only two full backups existed when more than 1 TB
of WAL filled the repository, so normal expiration had never become eligible.
The backup wrapper also checked the live PostgreSQL container before any
repository maintenance, making the full-repository/database-down combination
self-sustaining.

The durable database grew by less than 1 GiB during the same period. Relation
growth, XFS inode exhaustion, PostgreSQL bloat, and the configured 4 GiB WAL
checkpoint ceiling were not the capacity source.

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

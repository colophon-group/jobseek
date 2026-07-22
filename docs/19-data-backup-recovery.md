# Hetzner Data Backup and Recovery

This runbook covers the production PostgreSQL and Typesense data backups.
It does not treat a Hetzner server backup as an application-data backup.
PostgreSQL data lives on an attached Volume, which server backups exclude,
and Typesense requires an application-consistent snapshot before archival.

Do not record Storage Box usernames, hostnames, private keys, encryption
secrets, API keys, or resource IDs in this repository. Root-only deployment
configuration is under `/etc/jobseek-backup` on the relevant host.

## Protection model

| Data | Consistent source artifact | Off-host repository | Schedule | Retention |
|---|---|---|---|---|
| PostgreSQL | pgBackRest physical backup plus continuous WAL archive | encrypted pgBackRest SFTP repository | daily at 01:00 UTC; weekly full, otherwise differential | four full backup chains |
| Typesense | Typesense Snapshot API output | encrypted Restic SFTP repository | daily at 02:00 UTC | 14 daily and 4 weekly snapshots |

Recovery objectives:

| Data | RPO | RTO | Recovery owner |
|---|---|---|---|
| PostgreSQL | 5 minutes, using the latest base backup and archived WAL | 4 hours | Jobseek production operations |
| Typesense | 24 hours from backup; PostgreSQL remains the rebuild source of truth for newer crawler-owned state | 2 hours | Jobseek production operations |

The daily Codex error review is the notification owner: it must open or update
an actionable GitHub issue when a backup fails, becomes stale, or loses
telemetry coverage. The production operator owning that issue owns recovery
and escalation. Production operations also owns a restore drill at least once
per calendar quarter; attach redacted drill evidence to the tracking issue.

The two repositories use separate, home-directory-isolated Storage Box
subaccounts and separate SSH keys. The Storage Box is private to Hetzner,
delete-protected, and creates seven daily ZFS snapshots as secondary deletion
protection. Those ZFS snapshots are not a substitute for the backups above.

The repository encryption secrets are escrowed only in the protected GitHub
Actions `production` environment as:

- `HETZNER_POSTGRES_BACKUP_CIPHER_PASS`
- `HETZNER_TYPESENSE_RESTIC_PASSWORD`

Do not print or pass either secret on a command line. The host copies are
root-readable only.

## Installed components

Repository-owned files:

- `scripts/jobseek-data-backup.py`
- `deploy/backups/install-host.sh`
- `deploy/backups/postgresql/Dockerfile`
- `deploy/systemd/jobseek-postgresql-backup.{service,timer}`
- `deploy/systemd/jobseek-typesense-backup.{service,timer}`

Host state:

| Host | Runtime state |
|---|---|
| PostgreSQL | `/etc/jobseek-backup/postgresql`, `/var/lib/jobseek-backup/postgresql`, and `jobseek-postgres:16-pgbackrest` |
| Typesense | `/etc/jobseek-backup/typesense.env`, `/etc/jobseek-backup/typesense`, and `/var/lib/jobseek-backup/typesense` |

Both jobs atomically write a redacted JSON result and a Prometheus textfile
under `/var/lib/jobseek-backup/status`. A failed attempt preserves the time of
the last successful backup so a failed and a stale backup remain distinct.

## Installation and scheduling

Copy a checkout of the exact reviewed revision to `/opt/jobseek-backup`, then
install without starting a timer:

```bash
cd /opt/jobseek-backup
bash deploy/backups/install-host.sh postgresql
bash deploy/backups/install-host.sh typesense
```

The installer preserves the timer's current state unless `--start-timer` or
`--disable-timer` is explicitly supplied. A first installation therefore
cannot become active after a host reboot, while a later CI/CD sync cannot
silently stop a validated schedule. Start scheduling only after a manual
backup and isolated restore have passed:

```bash
bash deploy/backups/install-host.sh --start-timer postgresql
bash deploy/backups/install-host.sh --start-timer typesense
```

After merge, `.github/workflows/deploy-data-backups.yml` copies the reviewed
main-branch artifacts to both hosts and runs the installer in preserve mode.
It records the deployed commit without starting, stopping, enabling, or
disabling an existing timer. Deployment uses the same per-service lock as the
backup job and fails safely instead of replacing code during an active
backup. The production environment variables
`HETZNER_POSTGRES_HOST` and `HETZNER_TYPESENSE_HOST` select the two hosts; the
workflow reuses the existing Hetzner SSH deployment credential.

Confirm the effective schedule:

```bash
systemctl is-enabled jobseek-postgresql-backup.timer
systemctl is-active jobseek-postgresql-backup.timer
systemctl list-timers --all jobseek-postgresql-backup.timer --no-pager

systemctl is-enabled jobseek-typesense-backup.timer
systemctl is-active jobseek-typesense-backup.timer
systemctl list-timers --all jobseek-typesense-backup.timer --no-pager
```

## PostgreSQL backup operation

The production PostgreSQL image is built from the pinned digest in
`deploy/backups/postgresql/Dockerfile`. It retains PostgreSQL 16 and adds
pgBackRest plus the SFTP client. PostgreSQL must run with:

```text
wal_level=replica
max_wal_senders=3
archive_mode=on
archive_command=pgbackrest --stanza=jobseek archive-push %p
archive_timeout=60s
```

The same single maintenance restart raises the container limit from 2 to 4
GiB, `shared_buffers` from 512 MiB to 1 GiB, and `max_wal_size` from 1 to 4
GiB after the data Volume is expanded. It also sets a 15-minute checkpoint
timeout, 0.9 completion target, 1 GiB minimum WAL, and WAL compression. These
settings address the measured requested-checkpoint pressure and leave the
original 2 GiB/minimal-WAL container as the rollback target; they must not be
applied while the data Volume has only its former 1.8 GiB free.

The migration preserves the exact old container as a stopped rollback target:

```bash
/usr/local/sbin/jobseek-postgresql-enable-pgbackrest apply
```

On any failed health or pgBackRest check, the script automatically removes
the failed replacement and restarts the preserved container. An operator can
also invoke `rollback` explicitly. Run `finalize` only after the off-host full
backup and isolated restore have passed; until then the old container remains
stopped and references the same data directory without taking another copy.

The pgBackRest repository uses strict host-key fingerprint verification,
AES-256-CBC repository encryption, asynchronous WAL archiving, bundled/block
incremental storage, and Zstandard compression. Never weaken host-key
verification to make an SFTP connection succeed.

Run and verify a full backup:

```bash
/usr/local/sbin/jobseek-data-backup postgresql --backup-type full
docker exec --user postgres postgres pgbackrest --stanza=jobseek check
docker exec --user postgres postgres pgbackrest --stanza=jobseek info
journalctl -u jobseek-postgresql-backup.service -n 100 --no-pager
cat /var/lib/jobseek-backup/status/postgresql.json
```

Check WAL archival and capacity after any PostgreSQL restart or backup change:

```bash
docker exec postgres psql -U crawler -d crawler -Atc \
  "select archived_count, failed_count, last_archived_time, last_failed_time from pg_stat_archiver"
du -sh /var/lib/jobseek-backup/postgresql/spool
df -h /mnt/HC_Volume_105256309 /
```

Treat a growing archive failure count, stale `last_archived_time`, or a
growing spool as urgent. PostgreSQL preserves unarchived WAL, so an archive
failure can consume the already constrained data Volume.

## Typesense backup operation

The job asks the live Typesense process to create a consistent snapshot under
its container-local `/tmp` directory, copies the completed snapshot to a
host staging directory, uploads it to the encrypted Restic repository, runs
retention/pruning and `restic check`, then removes both temporary copies. It
does not stop or restart Typesense.

Run and verify a backup:

```bash
systemctl start jobseek-typesense-backup.service
systemctl status jobseek-typesense-backup.service --no-pager
journalctl -u jobseek-typesense-backup.service -n 100 --no-pager
cat /var/lib/jobseek-backup/status/typesense.json
set -a; . /etc/jobseek-backup/typesense.env; set +a
restic -o "sftp.command=${RESTIC_SFTP_COMMAND}" snapshots --tag jobseek-typesense
restic -o "sftp.command=${RESTIC_SFTP_COMMAND}" check
```

If upload or repository validation fails, the host staging copy is preserved
for diagnosis. Snapshot directories older than 48 hours are removed before a
later attempt. Never archive `/mnt/typesense-data` while Typesense is live.

## Isolated restore drills

A successful upload is not restore evidence. Perform both drills after
initial deployment and after material backup-format, credential, storage, or
major-version changes. Keep the restored services bound to loopback and use
temporary credentials. Do not connect workers, exporters, the web app, or the
Cloudflare tunnel to a restore drill.

### PostgreSQL

1. Restore the latest pgBackRest backup into a new host directory with enough
   free space; never restore over `/mnt/HC_Volume_105256309/pgdata`.
2. Start a temporary PostgreSQL 16 container bound only to
   `127.0.0.1:55432`, with the restored directory as its data directory.
3. Verify startup recovery reaches a consistent state, database and table
   counts are plausible, constraints can be read, and representative crawler
   application queries succeed.
4. Record backup label, recovery target/time, restored byte count, elapsed
   time, checks performed, and result without recording row contents or
   secrets.
5. Stop and remove the temporary container and restored data.

### Typesense

1. Restore the latest Restic snapshot into a new host directory; never write
   to `/mnt/typesense-data`.
2. Start `typesense/typesense:27.1` on a host other than the production
   Typesense machine, bound only to `127.0.0.1:18108`, with a temporary API
   key and the restored directory.
3. Compare collection/alias inventory, document counts, and representative
   document reads with production. Do not expose the drill through
   Cloudflare.
4. Record Restic snapshot ID/time, restored byte count, elapsed time, checks,
   and result without recording document contents or secrets.
5. Stop and remove the temporary container, restored data, and all temporary
   credentials and repository access.

## Failure and removal gates

Do not remove the existing Hetzner server backups until all of the following
are true for both services:

- the off-host backup completed and repository validation passed;
- an isolated restore using that repository passed;
- the timer is enabled and its next run is visible;
- failure and freshness status is included in the daily Codex error-review
  evidence and can create or update an actionable GitHub issue;
- recovery evidence and measured recovery time are recorded in the audit.

After the gate passes, disable server backups and delete the residual server
backup images for PostgreSQL and Typesense. Preserve the independent Storage
Box repositories and their secondary snapshots.

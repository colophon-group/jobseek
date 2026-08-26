# Hetzner Data Backup and Recovery

This runbook covers the authoritative crawler PostgreSQL, Typesense, and the
small provider-neutral web PostgreSQL data backups.
It does not treat a Hetzner server backup as an application-data backup.
PostgreSQL data lives on an attached Volume, which server backups exclude,
and Typesense requires an application-consistent snapshot before archival.

Do not record Storage Box usernames, hostnames, private keys, encryption
secrets, API keys, or resource IDs in this repository. Root-only deployment
configuration is under `/etc/jobseek-backup` on the relevant host.

## Protection model

| Data | Consistent source artifact | Off-host repository | Schedule | Retention |
|---|---|---|---|---|
| PostgreSQL | pgBackRest physical backup plus continuous WAL archive | AES-encrypted pgBackRest repository on a private, encrypted SMB 3 Storage Box mount | daily at 01:00 UTC; weekly full, otherwise differential | four full backups, seven differential backups, and continuous WAL for the two latest differentials |
| Typesense | Typesense Snapshot API output | encrypted Restic SFTP repository | daily at 02:00 UTC | 14 daily and 4 weekly snapshots |
| Web PostgreSQL | PostgreSQL 17 custom-format logical dump of an explicit, FK-closed web/support table allowlist | encrypted Restic SFTP repository, isolated by `jobseek-web-postgresql` host/tag | every 6 hours at :30 UTC | 30 daily, 12 weekly, and 12 monthly snapshots |

Recovery objectives:

| Data | RPO | RTO | Recovery owner |
|---|---|---|---|
| PostgreSQL | 5 minutes, using the latest base backup and archived WAL | 4 hours | Jobseek production operations |
| Typesense | 24 hours from backup; PostgreSQL remains the rebuild source of truth for newer crawler-owned state | 2 hours | Jobseek production operations |
| Web PostgreSQL | 6 hours | 2 hours | Jobseek production operations |

The daily Codex error review is the notification owner: it must open or update
an actionable GitHub issue when a backup fails, becomes stale, or loses
telemetry coverage. The production operator owning that issue owns recovery
and escalation. Production operations also owns a restore drill at least once
per calendar quarter; attach redacted drill evidence to the tracking issue.

The two repositories use separate, home-directory-isolated Storage Box
subaccounts and credentials. PostgreSQL uses a dedicated SMB credential;
Typesense uses a dedicated SSH key. The Storage Box and both subaccounts are
private to Hetzner, the box is delete-protected, and it creates seven daily
ZFS snapshots as secondary deletion protection. Those snapshots are not a
substitute for the backups above.

The repository encryption secrets are escrowed only in the protected GitHub
Actions `production` environment as:

- `HETZNER_POSTGRES_BACKUP_CIPHER_PASS`
- `HETZNER_TYPESENSE_RESTIC_PASSWORD`

The web logical backup reuses the encrypted Typesense Restic repository and
its protected SFTP transport, but has a separate host/tag and retention set.
The protected production `DATABASE_URL_UNPOOLED` secret is delivered during
installation into a systemd credential file; it is never stored in this
repository, printed, or placed on a command line.

Do not print or pass either secret on a command line. The host copies are
root-readable only.

## Installed components

Repository-owned files:

- `scripts/jobseek-data-backup.py`
- `deploy/backups/{deploy-remote,install-host-from-stdin,install-host}.sh`
- `deploy/backups/postgresql/Dockerfile`
- `deploy/backups/postgresql/{mount-repository,smoke-repository,restore-drill}.sh`
- `deploy/backups/typesense/restore-drill.sh`
- `deploy/systemd/jobseek-postgresql-backup-repository.service`
- `deploy/systemd/jobseek-postgresql-backup.{service,timer}`
- `deploy/systemd/jobseek-typesense-backup.{service,timer}`
- `deploy/backups/web-postgresql/{operations.py,restore-drill.sh}`
- `deploy/systemd/jobseek-web-postgresql-backup.{service,timer}`

Host state:

| Host | Runtime state |
|---|---|
| PostgreSQL | `/etc/jobseek-backup/postgresql`, `/var/lib/jobseek-backup/postgresql`, `/mnt/jobseek-postgresql-backups`, and `jobseek-postgres:16-pgbackrest` |
| Typesense | `/etc/jobseek-backup/typesense.env`, `/etc/jobseek-backup/typesense`, status under `/var/lib/jobseek-backup/status`, and direct snapshot staging on the dedicated `/mnt/jobseek-typesense-backup` filesystem |
| Web PostgreSQL (on the Typesense host) | `/etc/jobseek-backup/web-postgresql.env`, `/etc/jobseek-backup/web-postgresql.database-url`, root-only staging/drills under `/run/jobseek-backup/web-postgresql`, installed operation tooling under `/usr/local/sbin`, and aggregate/bound activation evidence under `/var/lib/jobseek-backup/status` |

All three jobs atomically write a redacted JSON result and a Prometheus textfile
under `/var/lib/jobseek-backup/status`. A failed attempt preserves the time of
the last successful backup so a failed and a stale backup remain distinct.
The root-owned fleet sampler republishes only the numeric status fields as
`jobseek_backup_*` metrics with stable host/service labels; it never forwards
the JSON error text. `DataBackupFailed` and `DataBackupStale` are owned by the
daily Codex route in `apps/crawler/alerts.yaml`. This establishes the telemetry
source. Bounded historical reads and GitHub issue delivery without a write
credential remain tracked by #5948; the owner-approved legacy-backup retirement
described below did not waive that follow-up.

## Initial production evidence (2026-07-22)

| Service | Backup evidence | Restore evidence |
|---|---|---|
| PostgreSQL | pgBackRest 2.59.0 full backup; 17.1 GiB database, 7.7 GiB repository delta, 199 seconds; repository status `ok`; continuous archive failure count remained zero | restored all 17.1 GiB to root-disk scratch space in 251 seconds, replayed encrypted archived WAL to a writable new timeline in about 210 seconds, then `pg_amcheck --parent-check` passed 384/384 relations and 2,147,497/2,147,497 pages; 2,447,190 postings were readable and the structural-null probe found zero invalid postings |
| Typesense | Snapshot API produced 1,348,345,503 bytes; encrypted Restic upload, prune, and check completed in about 164 seconds without restarting Typesense | verified Restic restore returned all 1,348,345,503 bytes in 13 seconds; an isolated Typesense 27.1 node became healthy, loaded all seven collections and aliases, matched stable collection counts, served representative reads, and returned live search results |

Temporary restore containers, data, keys and drill credentials were removed.
The reviewed revision was deployed from `main` to both hosts, and its exact
service units completed fresh production backups. PostgreSQL then completed a
19.1 GB differential scope with a 4.27 GB encrypted repository delta in 140
seconds; Typesense completed a 1,362,668,187-byte snapshot, encrypted upload,
prune, and repository check in 78 seconds. Both emitted successful atomic JSON
and Prometheus freshness records. PostgreSQL remained ready with zero archive
failures and Typesense remained healthy without a restart.

Both timers are enabled and their next jittered runs are visible. Delete and
rebuild protection is enabled on both data servers, delete protection is
enabled on the PostgreSQL Volume, and the validated pre-cutover PostgreSQL
container has been removed.

On 2026-07-23, the account owner explicitly directed retirement of the mistaken
server backups while #5948 was paused on account-owner acceptance of updated
Grafana terms. Immediately before the control-plane change, both native backup
jobs had succeeded that day, both timers were enabled, PostgreSQL WAL archival
had zero failures, the Storage Box and data resources were delete-protected,
both services were healthy with zero restarts/OOMs, and no backup alert was
firing. Disabling the two Hetzner backup schedules also removed all seven
server-bound backup images for each host. PostgreSQL and Typesense were not
restarted. The independent encrypted repositories and Storage Box snapshots
remain the recovery artifacts.

## Installation and scheduling

Copy a checkout of the exact reviewed revision to `/opt/jobseek-backup`, then
install without starting a timer:

```bash
cd /opt/jobseek-backup
bash deploy/backups/install-host.sh postgresql
bash deploy/backups/install-host.sh web-postgresql
```

The installer preserves the timer's current state unless `--start-timer` or
`--disable-timer` is explicitly supplied. A first installation therefore
cannot become active after a host reboot, while a later CI/CD sync cannot
silently stop a validated schedule. Start scheduling only after a manual
backup and isolated restore have passed:

```bash
bash deploy/backups/install-host.sh --start-timer postgresql
bash deploy/backups/install-host.sh --start-timer web-postgresql
```

Typesense installation is deliberately not a standalone command: first stage
the exact revision through `deploy-typesense-host.yml`, which quiesces the old
timer and writes the pending marker, then dispatch `deploy-data-backups.yml`
with `service=typesense` at the same revision. The installer always runs a
fresh direct-mount snapshot and starts the timer only after it passes.

`.github/workflows/deploy-data-backups.yml` is manual for every service. It
copies the explicitly dispatched revision and selects `all` or one service;
there is no push-triggered production mutation. Its protected transport
explicitly starts the PostgreSQL and Typesense timers, matching the production
post-install requirement that both durable backup schedules are enabled and
healthy. That also lets the next reviewed deployment recover a timer which a
failed rotation or repair deliberately left disabled. The retired web
PostgreSQL timer remains preserve-mode so deployment cannot resurrect it.
The web job shares the Typesense host only as an execution and encrypted-
repository location; it does not read Typesense data or credentials.
Deployment takes a host-wide deployment/identity lock before the per-service
data lock, so it cannot replace the shared backup runtime during any protected
operation or overlap an active backup for that service. The production
environment secrets
`HETZNER_POSTGRES_HOST` and `HETZNER_TYPESENSE_HOST` select the two hosts; the
workflow reuses the existing Hetzner SSH deployment credential and validates
both hosts against the pre-provisioned `HETZNER_BACKUP_KNOWN_HOSTS`. Artifacts
travel over native strict OpenSSH, and database/Typesense credentials travel
only as a root-only stdin payload to the reviewed host-side installer, never in
the remote command line. `ssh-keyscan` and runtime-downloaded SSH clients are
not used. Host addresses are secrets for log-redaction purposes even though
they are not authentication material. These are environment-scoped secrets,
so the workflow resolves them inside runtime steps after the main-only
`production` environment is attached; do not embed their values in
`strategy.matrix`, which GitHub expands earlier.

For Typesense, the protected `TYPESENSE_BACKUP_KEY` is authorization-probed
against authenticated `GET /stats.json` while the live host environment remains
untouched. The installer stages a complete root-owned `0600` candidate beside
the live file and compares exactly one `TYPESENSE_API_KEY` assignment. Every
staged Typesense deployment, including an unchanged key, proves the timer and
service quiesced and releases only the per-service data lock while retaining
the host-wide deployment lock. It runs a fresh snapshot, encrypted Restic
upload, retention prune, and repository check directly with the candidate
environment, validates the direct-mount/headroom evidence, then reacquires the
service lock before atomically committing a changed candidate. The outer
staged rollout starts the timer only after status and deployment-marker gates
pass. Rollback remains armed through those gates. Any failure restores a prior
root-only environment when it changed and leaves the timer disabled and
inactive; a lock or credential rollback failure is itself the primary hard
error and is never swallowed. A failed service, failed latest attempt, or stale
last success is fatal; the deployed revision is recorded only after those
checks pass.

The web installer receives `DATABASE_URL_UNPOOLED` only after the protected
production environment is attached. It atomically writes the URL to the
root-only systemd credential and copies only the three Restic transport fields
from the existing Typesense backup environment. The Typesense API key is not
available to the web backup service. A changed database URL or Restic setting
is transactional: the installer stages both root-only candidate files while
leaving live files untouched. When the web timer is enabled or active, it first
proves the timer disabled/inactive, releases only the service data lock, and
runs a fresh backup/freshness check with the candidate credential directory
and candidate Restic environment while retaining the host-wide deployment
lock. It reacquires the service lock before atomically moving both candidates
into place, then restores and verifies the exact prior timer state. A smoke or
lock-reacquisition failure leaves both live files byte-identical. An incomplete
two-file commit leaves the timer disabled fail-safe, and the next installer
reconciles only exact root-owned candidate directories. An unchanged candidate
does not run a deployment smoke, and an initially disabled staged timer remains
disabled so the protected manual backup/restore sequence remains the first
connection proof.

### Protected web PostgreSQL activation

When direct production SSH is unavailable, use
`.github/workflows/operate-web-postgresql-backup.yml` from `main`. Its first
job rejects the wrong original actor, rerun-triggering actor, ref, event, mode,
or token before any environment approval is requested. The second job attaches
the main-only `production-backup-operations` environment, requires owner
review, revalidates the dispatch, and binds artifacts. Only after that job
succeeds does a third job recheck both actors, attach the main-only `production`
environment, and read `HETZNER_TYPESENSE_HOST`,
`HETZNER_SSH_KEY`, and the pre-provisioned trusted host-key entries in
`HETZNER_TYPESENSE_KNOWN_HOSTS`. The dispatcher uses native OpenSSH with
strict host-key checking and never discovers trust with `ssh-keyscan`.

The authorization job checks out the exact dispatch SHA with full history,
derives the latest ancestor commit that touched the backup deployment
workflow's exact trigger-path set, and hashes the backup script, installed
operation helper, restore drill, service, and timer from the dispatch checkout.
The host helper requires the web-installer-specific
`/var/lib/jobseek-backup/web-postgresql-deployed-sha` marker to equal that latest
relevant commit and every installed artifact to equal its reviewed hash. The
service-specific marker is written only after the web matrix leg completes, so
another service on the shared host cannot attest the web deployment. An
unrelated later `main` commit therefore does not invalidate operations, while a
relevant change whose deployment has not completed fails closed. Backup and
restore evidence is persisted together in root-only
`web-postgresql-activation.json`, bound to that revision and artifact map, and
revalidated on every later dispatch.

The workflow does not receive the web database URI or Restic credentials;
those remain in root-only host files. It shares the backup deployment
concurrency group, runs no command with shell tracing, and publishes only
aggregate counts, sizes and timing. Repository and restore command output is
captured without being relayed into GitHub logs.

`backup`, `restore`, and `enable-timer` each rerun the same non-mutating host
readiness gate used by `verify`; the documented sequence is not trusted as
advisory state. Credential modes, exact Restic configuration, Docker, pinned
image, units, repository access, deployed identity, and current timer state
must still be valid at the point of every later operation.

Every protected operation holds the host-wide deployment/identity lock across
readiness, evidence validation, mutation, and its final identity check. Restore
also holds the web service data lock. Both open lock descriptions are inherited
and path-verified by the restore child, so a killed parent cannot release the
artifact boundary while the child can still invoke the shared backup runtime.

Each dispatch is main-only and requires the exact token for its selected mode:

| Mode | Confirmation | Effect |
|---|---|---|
| `verify` | `VERIFY-WEB-POSTGRESQL` | Read-only validation of installed code, root-only credential/config modes, pinned restore image, encrypted repository reachability, deployed-revision marker, and current timer state |
| `backup` | `RUN-WEB-POSTGRESQL-BACKUP` | Starts one systemd backup, requires fresh successful aggregate status, and proves the timer state did not change |
| `restore` | `RUN-WEB-POSTGRESQL-RESTORE-DRILL` | Requires a bound successful backup from the last nine hours, runs the private-network-only self-cleaning restore drill, matches its archive/count evidence to that backup, proves exact container/network/decrypted-directory removal, and proves the timer state did not change |
| `enable-timer` | `ENABLE-WEB-POSTGRESQL-TIMER` | Requires the timer to be disabled/inactive plus a fresh bound backup followed by a fresh successful restore of the same SHA-256-bound archive; starts the timer non-persistently, verifies active state, service health, a visible next run, and installed identity, then enables persistence as the final commit; handled failure rolls back to disabled |

Run the modes in that order for first activation. `verify`, `backup`, and
`restore` never enable or disable the timer. A failed or stale evidence file,
a restore that predates the latest backup, mismatched archive/count evidence,
the wrong confirmation, or a non-`main` dispatch fails closed. The direct host
commands below remain the break-glass/operator equivalents, not a way to skip
the activation gate.

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
the checksum-pinned pgBackRest 2.59.0 distribution. The image build runs the
upstream PostgreSQL backup/restore smoke suite and deliberately disables the
unused libssh2 transport. PostgreSQL must run with:

```text
wal_level=replica
max_wal_senders=3
archive_mode=on
archive_command=test -f /var/spool/pgbackrest/archive-enabled && flock -s /var/spool/pgbackrest/repository.lock pgbackrest --stanza=jobseek archive-push %p
archive_timeout=60s
```

Repository retention is deliberately split between backup sets and continuous
WAL. `repo1-retention-full=4` preserves four weekly full recovery points,
`repo1-retention-diff=7` preserves the latest week of daily differential
points, and `repo1-retention-archive=2` with archive type `diff` preserves
point-in-time recovery from the two latest differentials. Continuous WAL must
not inherit the four-full-backup setting: this workload can generate more than
100 GB of compressed WAL per day, so retaining four weeks can exhaust the 1 TB
repository before the fourth full backup exists.

`jobseek-data-backup postgresql` runs a networkless pgBackRest `expire` in a
separate read-only container before it requires the live PostgreSQL container
to be healthy. Archive-push holds a shared kernel lock and expiration holds the
exclusive form, so a process or host crash releases serialization without
stranding WAL archiving. The wrapper also uses the persistent archive sentinel
as a fail-closed compatibility hold for a container that predates the lock
contract. The repository mount alone is writable. This makes a full repository
recoverable by the next scheduled attempt even when PostgreSQL is already
down, and the same explicit retention options are passed to every backup so
its automatic expiration cannot drift from the host configuration. The host
installer atomically reconciles only the four retention keys and preserves the
adjacent repository coordinate and encryption secret verbatim.

During the initial cutover the sentinel is absent, so PostgreSQL retains WAL
without racing `archive-push` against repository stanza creation. The
migration script creates the stanza, writes the sentinel to the persistent
pgBackRest spool mount, and immediately runs `pgbackrest check`. Normal
container/host restarts preserve the validated sentinel. A fresh cutover
removes it before starting the replacement; until validation recreates it,
archival intentionally fails closed and retains WAL.

The same single maintenance restart raises the container limit from 2 to 4
GiB, `shared_buffers` from 512 MiB to 1 GiB, and `max_wal_size` from 1 to 4
GiB after the data Volume is expanded. It also sets a 15-minute checkpoint
timeout, 0.9 completion target, 1 GiB minimum WAL, and WAL compression. These
settings address the measured requested-checkpoint pressure and leave the
original 2 GiB/minimal-WAL container as the rollback target; they must not be
applied while the data Volume has only its former 1.8 GiB free.

The live container also requires `--shm-size 1g`. This is independent of
`shared_buffers`: it raises Docker's POSIX shared-memory mount above the unsafe
64 MiB default used by parallel-query dynamic shared memory, while the 4 GiB
container cgroup remains the total memory limit. Both this migration script and
the ingress replacement script validate Docker's configured value plus the
mounted `/dev/shm` capacity before accepting a replacement. See
[`16-hetzner-maintenance.md#postgresql-shared-memory`](16-hetzner-maintenance.md#postgresql-shared-memory)
for verification, alerting, and rollback.

The migration preserves the exact old container as a stopped rollback target:

```bash
/usr/local/sbin/jobseek-postgresql-enable-pgbackrest apply
```

On any failed health or pgBackRest check, the script automatically removes
the failed replacement and restarts the preserved container. An operator can
also invoke `rollback` explicitly. Run `finalize` only after the off-host full
backup and isolated restore have passed; until then the old container remains
stopped and references the same data directory without taking another copy.

The repository mount is owned by a dedicated systemd unit. It requires a
private-to-Hetzner Storage Box subaccount, SMB 3.1.1 transport encryption
(`seal`), hard I/O semantics, strict client caching, and CIFS symlink
emulation. The root-only credential and share coordinate live in
`storage-box.cifs` and `repository.env`; pgBackRest additionally applies
AES-256-CBC repository encryption. Asynchronous WAL archiving, bundled/block
incremental storage, and Zstandard compression remain enabled. The installer
refuses a mount missing the expected source, CIFS type, `seal`, `hard`, or
`mfsymlinks` option.

An isolated SFTP compatibility test reproduced a pgBackRest/libssh2
segmentation fault against the Storage Box on both 2.57.0 and 2.59.0. Do not
reintroduce that transport without a new isolated stanza-create, backup, and
restore proof. The mounted repository path is the supported production path.

Run and verify a full backup:

```bash
systemctl start jobseek-postgresql-backup.service
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
df -h /mnt/jobseek-postgresql-backups
```

Treat a growing archive failure count, stale `last_archived_time`, or a
growing spool as urgent. PostgreSQL preserves unarchived WAL, so an archive
failure can consume the already constrained data Volume.

Treat the repository itself as a bounded operational filesystem. Healthy
steady state is at least 35% free; the critical control also forecasts its
seven-day free-space trend. If retention is wrong or the repository is full:

1. preserve `pgbackrest info --output=json`, filesystem, backup-status, WAL,
   and container evidence;
2. stop a futile PostgreSQL restart loop without deleting any database/WAL
   file;
3. run pgBackRest `expire --dry-run` with the reviewed archive-retention
   options and verify that no backup set is selected;
4. run the same expiration under `/run/jobseek-data-backup-postgresql.lock`;
5. verify repository free space and `pgbackrest info` before restoring the
   PostgreSQL restart policy; and
6. require archive catch-up, a fresh backup, and an isolated restore drill.

Never delete repository paths with `rm` or manually remove `pg_wal`. Only
pgBackRest may expire repository objects, because its backup metadata defines
the safe archive boundary.

The Storage Box automatic snapshot plan retains two daily snapshots. Provider
snapshots are a short secondary deletion guard, not additional pgBackRest
recovery points, and they consume the same 1 TB quota. Do not increase that
window without including high-churn archived WAL in the capacity forecast. If
snapshots predate emergency pgBackRest expiration, every one of them can retain
the obsolete blocks; inspect the exact provider snapshot set and repository
statistics before deleting it, then re-enable the two-snapshot plan only after
the repository and fresh backup are healthy.

## Typesense backup operation

The job asks the live Typesense process to create a consistent snapshot
directly on the isolated `/jobseek-snapshots` bind mount, atomically promotes
that directory to a host packet, uploads it to the encrypted Restic repository,
runs retention/pruning and `restic check`, then removes the successful packet.
The packet keeps the untouched Typesense checkpoint under `data/`; the job
verifies that the promoted checkpoint is non-empty before upload. It does not
stop or restart Typesense.

The backup validates the seven-alias contract immediately before and after
the Snapshot API call. Both observations must contain exactly the required
aliases, every target must resolve to that exact physical collection, and the
alias mappings must match. Live document counts are recorded after the
snapshot as operational evidence, with
`collection_documents_observation=live_after_snapshot`, but count movement is
not a consistency failure: ordinary exporter and watchlist writes may continue
while Typesense creates its own atomic checkpoint. If the alias contract is
temporarily incomplete or changes during the boundary, that checkpoint is
discarded and the complete operation is retried after five seconds, up to
three attempts. A contract that does not stabilize then fails closed before
copy or upload; retries never silently select one side of a concurrent alias
cutover. Each attempt is created directly under
`/mnt/jobseek-typesense-backup/staging/.attempts` through the container's
`/jobseek-snapshots` bind mount. The stable attempt is atomically renamed into
the backup packet before hashing and upload, so peak local snapshot copies is
one. The job refuses to start unless this path is an exact root-owned `0700`
mount on a device separate from both `/` and `/mnt/typesense-data`, has at
least 20 GiB capacity, retains the live-snapshot estimate plus 4 GiB growth and
8 GiB free-floor headroom before the call, retains growth plus the floor after
the call, and is the container's labelled writable snapshot mount. The
container must also expose the exact reviewed 6 GiB hard limit, 5 GiB
reservation, and 6 GiB memory-plus-swap ceiling while durable current, peak,
and event counters are retained.

During restore, aliases and collection counts are derived from the isolated
restored node and cross-checked against wildcard reads, so the restore inventory
belongs to the artifact instead of a later live-source observation.

Every reviewed Typesense backup deployment quiesces the timer and runs one
fresh direct-mount smoke from the staged candidate. It writes the deployment
marker, clears the pending contract marker, and restores or starts the timer
only after fresh atomic status is proven. This also recovers an already-latched
failed unit or stale status; a failed smoke leaves the timer disabled for
explicit operator review.

The API credential is a dedicated generated key, delivered from the protected
`TYPESENSE_BACKUP_KEY` GitHub environment secret into the root-owned
`/etc/jobseek-backup/typesense.env` file as `TYPESENSE_API_KEY`. It is not the
server bootstrap key and is not shared with the crawler. Typesense 27.1
returned 401 for generated keys limited to `operations:snapshot` and
`operations:*`, so this consumer currently requires a generated wildcard key.
That version-specific exception is constrained by root-only file/service
access and remains independently revocable. Re-test and narrow the action
scope at the next Typesense upgrade.

Treat these as separate signals:

1. **Process state:** Docker reports the container running with no OOM/restart.
2. **API readiness:** unauthenticated `GET /health` returns `{"ok": true}`.
3. **Backup authorization:** the installed backup key can authenticate
   `GET /stats.json`; this does not by itself prove that a backup ran.
4. **Backup execution:** the systemd attempt succeeds and the atomic status
   reports a fresh successful snapshot, Restic upload/prune, and repository
   check.
5. **Restore validity:** the newest off-host artifact starts as an isolated
   node and passes inventory, query, and disposable write/read/delete checks.

A healthy API can coexist with an unauthorized or stale backup. Conversely,
startup may temporarily return 503 while a valid restored snapshot reloads.
Do not infer one signal from another.

Run and verify a backup:

```bash
systemctl start jobseek-typesense-backup.service
systemctl status jobseek-typesense-backup.service --no-pager
journalctl -u jobseek-typesense-backup.service -n 100 --no-pager
cat /var/lib/jobseek-backup/status/typesense.json
findmnt --mountpoint=/mnt/jobseek-typesense-backup
df -h /mnt/jobseek-typesense-backup /
docker inspect typesense --format \
  'oom={{.State.OOMKilled}} restarts={{.RestartCount}} memory={{.HostConfig.Memory}} reservation={{.HostConfig.MemoryReservation}} swap={{.HostConfig.MemorySwap}}'
set -a; . /etc/jobseek-backup/typesense.env; set +a
restic -o "sftp.command=${RESTIC_SFTP_COMMAND}" snapshots --tag jobseek-typesense
restic -o "sftp.command=${RESTIC_SFTP_COMMAND}" check
```

If upload or repository validation fails, the single host staging copy is
preserved for diagnosis. A later attempt fails closed while any preserved
packet remains, so `snapshot_peak_local_copies=1` is an observed invariant, not
a hard-coded fiction. Resolve or retain the packet explicitly; automatic age
cleanup applies after 48 hours. A post-copy headroom failure removes its new
packet immediately to restore the protected reserve. Never archive
`/mnt/typesense-data` while Typesense is live, and never redirect snapshot
staging back onto `/` to bypass the mount or free-space gate.

## Web PostgreSQL backup operation

The web backup is a portability and Free-plan recovery artifact, not a second
crawler mirror. A digest-pinned PostgreSQL 17 client creates a custom-format
logical dump of this exact boundary:

- Better Auth: `user`, `session`, `account`, `verification`;
- user/product state: `user_preferences`, `saved_job`,
  `application_interview`, `followed_company`, `company_request`,
  `watchlist`, `watchlist_company`, `hiring_signal`, and `outreach_draft`;
- small FK support: `industry`, `company`, and `job_board`; and
- migration state: `drizzle.__drizzle_migrations`.

`job_posting`, crawler taxonomies, `enrich_batch`, the unused Stripe
`subscription` table, and all Murmur tables are excluded. Before dumping, the
job queries PostgreSQL's FK catalog and refuses to run if any included table
points to an excluded table. In particular, the first production run is gated
on contract migration `0085_saved_job_snapshot_contract`. That migration runs
only after the 0084 expand phase and snapshot-writing app release have been
verified, then removes the remaining `saved_job -> job_posting` FK after
catching up only absent required fields, asserting every required saved-posting
snapshot, and installing the final NOT NULL/nonblank contract. The backup
preflight requires the exact 0085 ledger hash and final saved-job catalog, not
merely the absence of an external FK.

The job fingerprints every allowlisted table as a row count plus a deterministic
aggregate hash and records the Drizzle migration sequence state. It runs the
dump from a serializable, deferrable snapshot, fingerprints again, and rejects
a backup if the source changed during that small window. Because PostgreSQL
table-filtered dumps do not include their containing schemas, the packet also
contains a fixed `bootstrap.sql` for the non-public `drizzle` schema. The job
validates the custom archive with `pg_restore --list`, records SHA-256 checksums
and fingerprints in a root-only manifest, uploads the three-file packet through
Restic, applies retention, and runs repository validation. Status and logs
expose only aggregate counts, bytes, hashes, and timing—never row contents,
credentials, addresses, or user identifiers.

The plaintext dump exists only in the root-only systemd runtime directory
under `/run`; it is deleted after a successful upload and disappears on reboot.
A failed upload keeps the runtime packet for bounded diagnosis, and the next
attempt removes runtime packets older than 48 hours. No plaintext web backup is
persisted to the host filesystem outside that volatile staging window.
The database URI is injected into the short-lived PostgreSQL 17 client
container and expanded there through an explicit `--dbname` argument.
`PGDATABASE` must not carry the URI: the client treats that environment value
as a literal database name and otherwise falls back to its local Unix socket.

The client image is also a protected host dependency. Installation pulls the
exact `postgres:17-alpine@sha256:...` reference and creates the stopped,
networkless, read-only
`jobseek-web-postgresql-backup-image-lease` container. That container has no
bind mounts, credentials, or running process. A 64-KiB non-persistent tmpfs
overrides the upstream image's declared data volume so creating the lease does
not allocate a durable anonymous volume. Its only operational purpose is to
keep the immutable image reachable to Docker's unused-image collector. Both
normal age-based collection and the below-5-GiB `docker image prune --all`
path therefore retain the digest. The backup keeps `--pull=never` and verifies
the exact image, stopped lease, and ownership label before contacting the
database. A mutable tag is never a recovery fallback.

The minute host sampler publishes
`jobseek_backup_helper_image_available{service="web-postgresql"}` and
`jobseek_backup_helper_image_gc_protected{service="web-postgresql"}`.
`WebPostgreSQLBackupHelperImageUnprotected` fires after three minutes, before
the six-hour backup schedule can breach its nine-hour freshness threshold.
Check the contract without printing configuration or credentials:

```bash
image='postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193'
docker image inspect "$image" >/dev/null
docker container inspect \
  --format '{{.Config.Image}}|{{.State.Running}}|{{index .Config.Labels "jobseek.backup.helper-image"}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}|{{json .HostConfig.SecurityOpt}}|{{json .HostConfig.Tmpfs}}|{{json .Mounts}}|{{json .Config.Entrypoint}}' \
  jobseek-web-postgresql-backup-image-lease
```

The second command must return the exact digest followed by
`false|web-postgresql|none|true|["ALL"]|["no-new-privileges:true"]|{"/var/lib/postgresql/data":"rw,noexec,nosuid,nodev,size=65536"}|[]|["/bin/true"]`.
If either check fails, preserve current backup status and Docker GC journal
evidence, then rerun the reviewed `web-postgresql` backup deployment. The
installer performs a bounded pull by the same digest and atomically reconciles
only its labelled, stopped lease. Do not create a mutable tag, start the lease,
or force-remove an unrecognised container.

If the final canonical rename fails, the installer exits non-zero and leaves a
labelled `.candidate.<pid>` container in place. This deliberately keeps the
digest outside Docker GC while the missing canonical lease remains visible to
readiness checks and alerting; rerun the reviewed deployment to reconcile it.

Run the first backup manually while its timer is disabled:

```bash
systemctl start jobseek-web-postgresql-backup.service
systemctl status jobseek-web-postgresql-backup.service --no-pager
journalctl -u jobseek-web-postgresql-backup.service -n 100 --no-pager
cat /var/lib/jobseek-backup/status/web-postgresql.json
set -a; . /etc/jobseek-backup/web-postgresql.env; set +a
restic -o "sftp.command=${RESTIC_SFTP_COMMAND}" snapshots \
  --tag jobseek-web-postgresql --host jobseek-web-postgresql
```

After deploying or repairing the helper-image lease, completion evidence must
include all of the following; repository tests alone do not close the recovery
incident:

1. both helper-image metrics are `1` and the helper-image alert is inactive;
2. a new encrypted backup succeeds, its atomic status is successful and less
   than nine hours old, and `restic check` passes;
3. that exact new artifact passes the isolated schema/data, Better Auth, saved
   job, and product-read restore drill;
4. the service is not failed and the six-hour timer is enabled, active, and has
   a visible next run; and
5. two ordinary timer cycles succeed across at least one hourly Docker GC run.

Do not enable the timer until this backup and the clean restore below pass.
`DataBackupStale` uses a nine-hour threshold for this six-hour schedule; the
daily PostgreSQL and Typesense services keep their 36-hour threshold. The host
sampler treats the web timer and its status as optional while the timer is
disabled, then automatically makes both required as soon as the timer is
enabled. This keeps the staged installation quiet without weakening the live
failure/freshness gate.

## Isolated restore drills

A successful upload is not restore evidence. Perform all relevant drills after
initial deployment and after material backup-format, credential, storage, or
major-version changes. Keep restored services unexposed: bind to loopback only
when a host port is required, and otherwise use an internal private network.
Use temporary credentials. Do not connect workers, exporters, the web app, or
the Cloudflare tunnel to a restore drill.

### PostgreSQL

1. Run `/usr/local/sbin/jobseek-postgresql-restore-drill`. It restores into a
   unique directory on a filesystem with enough free space and refuses to use
   the live Volume path.
2. The script starts a temporary PostgreSQL 16 container with Docker networking
   disabled and `archive_mode=off`; the private repository mount is read only
   from the recovery process.
3. It requires startup recovery to reach a writable consistent state, checks
   version/archive state and key counts, and runs `pg_amcheck` over every heap
   and B-tree relation with parent checks.
4. Record backup label, recovery target/time, restored byte count, elapsed
   time, checks performed, and result without recording row contents or
   secrets.
5. The script removes the temporary container and restored data on both
   success and failure.

### Typesense

1. Record the exact Restic snapshot ID selected for the drill. A redacted live
   inventory can help diagnose drift, but counts captured after a live-write
   snapshot are not expected to equal that checkpoint.
2. Copy `deploy/backups/typesense/restore-drill.sh` plus temporary root-only
   Restic access to a recovery host that has Docker and at least 4 GiB free.
   Use the same or a newer Restic version than the backup host; older clients
   can reject the repository format. The helper refuses to run if a container
   named `typesense` exists.
3. Set `JOBSEEK_TYPESENSE_RESTORE_ENV`, then run the helper with the exact
   snapshot ID. Set `JOBSEEK_TYPESENSE_EXPECTED_INVENTORY` only for inventory
   captured at a proven quiescent checkpoint boundary. It restores only into
   its unique temporary root and binds Typesense only to `127.0.0.1:18108`.
4. The helper requires health and single-node leadership, the exact alias set,
   nonnegative collection counts read from the restored checkpoint, a
   representative job-posting search, and a disposable collection
   write/read/delete. It emits only snapshot metadata, byte/count totals,
   duration, and named checks.
5. Its exit trap force-removes the isolated container, restored data, and
   generated API key on success, failure, or interruption. Remove the
   temporary Restic files from the recovery host after copying the redacted
   result to incident evidence. Never run the drill beside production, write
   to `/mnt/typesense-data`, or expose it through Cloudflare.

### Web PostgreSQL

1. Run `/usr/local/sbin/jobseek-web-postgresql-restore-drill` on the Typesense
   host. It holds the same lock as the backup and restores the latest encrypted
   `jobseek-web-postgresql` snapshot into a unique root-only directory.
2. The script starts clean digest-pinned PostgreSQL 17 with a temporary data
   filesystem on a unique `--internal` Docker network and publishes no host
   port. It generates an ephemeral random password in root-only files, uses
   `POSTGRES_PASSWORD_FILE` plus a mounted `pgpass` file, and passes no live
   database credential or password value through Docker metadata.
3. The checksum-bound bootstrap creates only the `drizzle` schema, then
   `pg_restore --exit-on-error` recreates the selected tables, data, indexes,
   sequence, and constraints. Its short-lived verifier clients join only that
   internal network and authenticate from the mounted `pgpass` file. The
   verifier checks both SHA-256 checksums,
   exact per-table row-count/hash parity, and migration-sequence parity against
   the encrypted manifest.
4. A rollback-only mutation smoke exercises Better Auth user/session/account
   rows, preferences, saved jobs/interviews, followed companies, watchlists,
   company requests, and hiring/outreach constraints.
5. The script atomically records aggregate drill evidence in
   `/var/lib/jobseek-backup/status/web-postgresql-restore.json`, then removes
   the exact container, internal network, restored archive, credential files,
   and temporary database on success, failure, or handled signal. The protected
   parent passes its held service-data and deployment/identity lock descriptors
   into the restore process,
   terminates and reaps the exact process group on timeout, and will not accept
   evidence until Docker and directory absence are proven. A later run cleans
   only stale service-labeled restore resources under that same lock, covering
   a prior parent or child `SIGKILL`. Attach the redacted evidence to #6169
   before the mirror purge.

## Failure and removal gates

The normal replacement gate for any future legacy backup retirement is:

- the off-host backup completed and repository validation passed;
- an isolated restore using that repository passed;
- the timer is enabled and its next run is visible;
- failure and freshness status is included in the daily Codex error-review
  evidence and can create or update an actionable GitHub issue;
- recovery evidence and measured recovery time are recorded in the audit.

For the Supabase Pro-to-Free downgrade, the web database gate additionally
requires: contract migration `0085_saved_job_snapshot_contract` deployed with
every existing saved row populated and the posting FK removed; one successful
`web-postgresql` backup; one clean restore with exact fingerprints and mutation
smoke; the six-hour timer enabled with a visible next run; and live
failure/freshness telemetry. Only then may #6170 drop crawler-mirror data.

Current state as of 2026-07-23: off-host backups, repository validation,
isolated restores, measured recovery evidence, enabled schedules, visible next
runs, resource protection, and live Grafana backup series have passed. At the
account owner's explicit direction, the two mistaken Hetzner backup schedules
were disabled before #5948's daily issue-delivery proof; the provider action
also removed all residual server-backup images. This is a recorded,
owner-approved exception, not a relaxation of the normal replacement gate.

Until #5948 completes, an operator must inspect the atomic backup status,
repository validation, timers, WAL archive state, and `DataBackupFailed` /
`DataBackupStale` rule state during production maintenance and backup incident
review. A failure or stale result is critical because the native encrypted
repositories are now the only recovery artifacts. Do not re-enable OS backups
as a substitute for repairing the native data-backup path. Preserve the
independent Storage Box repositories and their secondary snapshots.

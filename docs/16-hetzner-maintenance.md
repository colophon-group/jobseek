# Hetzner Maintenance

Operational runbook for the Jobseek Hetzner machines.

## Machines

Credentials and current IPs live in `apps/crawler/.env.local`; do not hardcode
secrets into commands or documentation.

| Role | Host variable | Main workload |
|------|---------------|---------------|
| Crawler | `CRAWLER_BROWSER_IPv4` | Workers, browser worker, exporter, drain, Redis, Alloy, murmur shim |
| Postgres | `POSTGRESQL_LOCAL_IPv4` | Local crawler Postgres |
| Typesense | `TYPESENSE_IPv4` | Typesense and `cloudflared` |

PostgreSQL and Typesense data protection is documented separately in
[`19-data-backup-recovery.md`](19-data-backup-recovery.md). That runbook is
the source of truth for backup scheduling, validation, restore drills, and
the removal gate for legacy server backups.

SSH pattern:

```bash
ssh -i ~/.ssh/hetzner_deploy root@<HOST>
```

## Typesense Host Credentials

Typesense and its Cloudflare Tunnel have a repo-owned, manually promoted host
surface:

- [`install-host.sh`](../deploy/typesense-host/install-host.sh) performs the
  locked, health-gated, rollback-capable transition;
- [`cloudflared.service`](../deploy/systemd/cloudflared.service) runs as an
  unprivileged service and uses systemd `LoadCredential`;
- [`verify-typesense-host-credentials.py`](../scripts/verify-typesense-host-credentials.py)
  emits only boolean conformance evidence; and
- [`deploy-typesense-host.yml`](../.github/workflows/deploy-typesense-host.yml)
  validates on relevant pushes but mutates production only on an explicit
  `workflow_dispatch`.

The Typesense container receives one argument:
`--config=/run/secrets/typesense-server.ini`. Its bind-mounted source is
`/etc/jobseek-typesense/typesense-server.ini`, owned by root with mode `0600`.
The Cloudflare token source is
`/etc/jobseek-typesense/cloudflare-tunnel-token`, also root-owned `0600`;
systemd copies it into the service credential directory for the dedicated
`cloudflared` user. Neither credential may appear in Docker environment
metadata, Docker/process arguments, a systemd unit, or world-readable host
state.

The protected `production` environment owns four deployment secrets:

| secret | consumer and scope |
|---|---|
| `TYPESENSE_BOOTSTRAP_KEY` | starts the Typesense server only; never passed to crawler, web, or backup workloads |
| `TYPESENSE_OPERATIONS_KEY` | generated/revocable crawler key with `collections:*`, `documents:*`, and `aliases:*` on all collections plus `metrics.json:list` |
| `TYPESENSE_BACKUP_KEY` | generated/revocable wildcard key confined to the root-owned backup service; Typesense 27.1 rejected narrower snapshot scopes |
| `CLOUDFLARE_TUNNEL_TOKEN` | one named Cloudflare Tunnel only |

For the initial transition, create the two generated Typesense consumer keys
and set all four protected secrets first. Merge and verify crawler and backup
deployments before changing the host. Then dispatch the reviewed `main`
revision one independent rollback boundary at a time:

```bash
gh workflow run deploy-typesense-host.yml \
  --ref main \
  -f component=typesense
gh workflow run deploy-typesense-host.yml \
  --ref main \
  -f component=cloudflared
```

The Typesense step requires successful backup evidence no older than 36 hours
and refuses to run while `jobseek-typesense-backup.service` is active. It
pulls the pinned image before stopping the container, waits up to 60 seconds
for a graceful stop, restores the prior config on failure, and requires local
health plus a bootstrap-key admin probe. The tunnel step preserves the prior
unit/token, restarts only `cloudflared`, and requires both systemd readiness
and public tunnel health. A repeat dispatch compares the credential file and
service contract and skips each conformant restart.

Rotate in dependency order:

1. create a new generated consumer key;
2. update its protected GitHub secret and deploy that consumer;
3. prove intended operations succeed and privileged operations fail;
4. delete the superseded generated key;
5. generate a new random bootstrap key, update
   `TYPESENSE_BOOTSTRAP_KEY`, dispatch `component=typesense`, and prove the old
   bootstrap key returns 401; and
6. rotate the named Cloudflare Tunnel token in the Cloudflare control plane,
   update `CLOUDFLARE_TUNNEL_TOKEN`, dispatch `component=cloudflared`, and
   prove the old token differs and the new connector/public API path is
   healthy.

Cloudflare token rotation prevents the old token from starting new
connectors; an already running connector remains until restarted. Update the
protected secret before dispatching the restart. Never rotate a Typesense
bootstrap key before all generated consumer keys are independently working,
because changing the server bootstrap invalidates the old bootstrap access
immediately.

Read-only, redacted verification:

```bash
/usr/local/sbin/jobseek-verify-typesense-host-credentials
systemctl is-active cloudflared.service
docker inspect typesense --format \
  'running={{.State.Running}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} cmd={{json .Config.Cmd}}'
curl --fail --silent http://127.0.0.1:8108/health
curl --fail --silent https://typesense.colophon-group.org/health
```

Do not manually recreate Typesense with `--api-key`, put a token directly in
`ExecStart`, or print either root-only credential file. Use a component
dispatch for recovery; its transaction and conformance checks are the
supported restart path.

## Ingress and SSH Baseline

The repository-owned ingress source of truth is:

- [`manage-hetzner-ingress.py`](../scripts/manage-hetzner-ingress.py) for the
  Hetzner Cloud Firewall attached to the three non-Murmur production servers;
- [`install-host.sh`](../deploy/networking/install-host.sh) for UFW and sshd;
- [`harden-postgresql.sh`](../deploy/networking/harden-postgresql.sh) for the
  PostgreSQL listener and exact HBA;
- [`jobseek-ingress-conformance.py`](../scripts/jobseek-ingress-conformance.py)
  for redacted host evidence; and
- [`deploy-hetzner-ingress.yml`](../.github/workflows/deploy-hetzner-ingress.yml)
  for protected audit and apply operations.

The protected `production` environment stores `HETZNER_API_TOKEN`,
`HETZNER_HOST`, `HETZNER_POSTGRES_HOST`, `HETZNER_TYPESENSE_HOST`, and
`HETZNER_SSH_KEY` as secrets. Host addresses are secrets for log-redaction
purposes even though they are not authentication material. Do not convert them
to GitHub variables: Actions prints ordinary variables in step environments.
The inventory helper emits GitHub masking commands before exporting derived
private addresses; suppressing that output would disable the masks.

The public Hetzner firewall is default-deny inbound and allows only TCP 22 and
ICMP over IPv4/IPv6. It has no outbound rules, so Hetzner's default outbound
allow behavior remains in effect for backups, Grafana, crawler traffic, and
Cloudflare Tunnel. Hetzner Cloud Firewalls do not filter private-network
traffic, so every host also runs UFW with default-deny inbound and
default-allow outbound:

| role | additional private ingress |
|---|---|
| crawler | none |
| PostgreSQL | TCP 5432 from the crawler's exact private IPv4 only |
| Typesense | TCP 8108 from the crawler's exact private IPv4 only |

SSH is key-only, keeps `root` as the CI/CD break-glass identity, permits
`deploy` only where it already has an authorized key, and disables forwarding,
tunnels, and user-supplied environments. A public SSH allowlist is
intentionally not required. The PostgreSQL host's root password is locked only
after a non-empty root `authorized_keys` file and valid effective sshd config
have been proved. Never remove the protected `HETZNER_SSH_KEY` secret before a
replacement break-glass path is tested.

Crawler application metrics bind to loopback by default. Compose uses host
networking, so local Alloy can still scrape ports 9093–9098 without exposing
them on a host interface. PostgreSQL binds only to loopback and its private
address. Its exact HBA permits the `crawler` and
`jobseek_labeller_readonly` roles, on the `crawler` database, from loopback and
the exact crawler private address, using SCRAM-SHA-256.

PostgreSQL TLS is not required on this local-crawler data path under the
current threat model: the database contains crawler-source job data rather
than end-user/auth data, the service is bound to the private interface, both
provider and host boundaries deny public access, the HBA admits one source,
and SCRAM prevents plaintext password authentication. Hetzner private-network
traffic is not encrypted, so this is not a general exception for sensitive
data. Adding user, authentication, billing, or other confidential data to this
database requires a separate certificate lifecycle and `verify-full` client
cutover before that data is admitted.

Run the read-only production audit from GitHub Actions first:

```bash
gh workflow run deploy-hetzner-ingress.yml \
  --ref main \
  -f action=audit
```

An apply is deliberately ordered to limit lockout and downtime:

1. validate and copy the exact reviewed revision;
2. stage sshd/UFW independently on all hosts, require the same exact host-only
   conformance used at commit, and retain a 15-minute automatic rollback timer
   for each host;
3. inspect the full PostgreSQL data-plane contract. If it is already exact,
   skip the container handoff; otherwise require a fresh successful backup,
   retain the original stopped container, and replace PostgreSQL with the same
   image/mount/resource contract but a private listener and exact HBA;
4. prove an actual query and Typesense health request from the crawler's live
   exporter configuration over the private paths and a fresh SSH session;
5. commit host transactions only after conformance passes; and
6. attach the provider firewall last, then externally prove SSH remains open
   and every known service/metrics port is closed.

Typesense is not restarted or reconfigured by this workflow. PostgreSQL is the
only possible workload handoff, and a repeat apply does not replace it when
the exact listener, HBA, repository config, shared-memory, and authentication
contract already passes. Any failed stage rolls itself back; a cross-host path
failure immediately rolls back every staged host; failed commits roll back any
transaction left pending; and the independent systemd timers remain armed
until commit. Provider-firewall changes use a root-only runner-temporary state
file and restore the previous rules/attachments if apply or external
verification fails.

OpenSSH may emit one effective `allowusers` line per configured user. The
conformance parser unions only those repeated allowlist directives; every
other security directive must appear exactly once with its required value.
Any additional allowed user or conflicting duplicate setting remains
noncompliant.

Apply only the reviewed revision on `main`:

```bash
gh workflow run deploy-hetzner-ingress.yml \
  --ref main \
  -f action=apply
```

Before the first apply, this audit is expected to exit nonzero while still
emitting the redacted control evidence. After maintenance, rerun
`action=audit`: it exits successfully only when all three hosts, the exact
provider policy, and the external port probes are compliant. External
verification and logs intentionally omit addresses,
resource IDs, credentials, connection strings, and raw HBA contents. Future
PostgreSQL container migrations source the root-owned
`/etc/jobseek-ingress/postgresql-network.env`; removing or bypassing that file
would regress the listener to a wildcard and must fail review.

## PostgreSQL Shared Memory

The live PostgreSQL container contract includes a 4 GiB memory cgroup and a
separate 1 GiB `/dev/shm` ceiling. Docker's default 64 MiB shared-memory mount
is not acceptable for this workload: PostgreSQL uses POSIX dynamic shared
memory for parallel queries, and reaching that mount limit raises `ENOSPC`
even when the host root filesystem and host `/dev/shm` have ample free space.
`--shm-size 1g` is a capacity ceiling, not a reservation; it does not allocate
1 GiB at container start. The existing cgroup remains the total memory safety
boundary.

Both repo-owned live-container creation paths enforce the same contract:

- `deploy/networking/harden-postgresql.sh`, used by the protected ingress
  transaction; and
- `deploy/backups/postgresql/migrate-container.sh`, used for the pgBackRest
  image migration and future recovery of that deployment surface.

Each path checks both Docker's configured `HostConfig.ShmSize` and the
capacity actually mounted at `/dev/shm` before accepting the replacement.
The redacted ingress conformance audit also requires at least 1 GiB. Never
recreate the production container with an ad hoc `docker run`; doing so can
silently restore Docker's 64 MiB default.

Read-only verification:

```bash
docker inspect postgres \
  --format 'configured_bytes={{.HostConfig.ShmSize}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}'
docker exec postgres df -h /dev/shm
docker stats --no-stream postgres
```

Healthy state has `configured_bytes=1073741824`, a 1 GiB mounted capacity,
no OOM flag, and adequate free capacity under normal parallel load. The host
sampler publishes configured/capacity/used/available byte gauges. The
`PostgreSQLSharedMemoryPressure` rule routes to the daily Codex error review
if the configured contract regresses or available capacity remains below 15%
for five minutes.

For an unsafe live contract, use the protected `action=apply` ingress workflow.
It requires a fresh successful PostgreSQL backup, preserves the old container
as the rollback target, arms a 15-minute automatic rollback, performs the only
database handoff in that workflow, proves private-path/readiness/pgBackRest
health, and commits only after cross-host validation. Do not merely restart the
existing container: Docker cannot change a container's shared-memory mount in
place.

Crawler monitor and scrape exceptions are rescheduled through Redis with a
five-minute error backoff, so transient database write failures remain
retryable. After remediation, verify the shared-memory error count no longer
increases, workers drain the retried tasks, PostgreSQL remains below its 4 GiB
cgroup limit, archive failure count stays flat, and no container records an OOM
or restart. Do not replay task identifiers manually unless queue and database
evidence proves the normal reschedule path failed.

## PostgreSQL Capacity and Checkpoint Pressure

The authoritative PostgreSQL database lives on the attached XFS data Volume,
not on the server root disk. The Volume was expanded online from 20 to 40 GiB
on 2026-07-22 after a transaction-consistent encrypted checkpoint passed. The
provider action cannot be reversed in place. Current and future expansion must
therefore preserve the same sequence: fresh backup and restore evidence,
recorded pre-change capacity, provider resize, online `xfs_growfs`, PostgreSQL
and archive verification, then recorded post-change capacity. Never use a
server backup as a substitute; server images do not contain this Volume.

The live PostgreSQL contract is deliberately consistent across
`deploy/backups/postgresql/migrate-container.sh` and
`deploy/networking/harden-postgresql.sh`: 4 GiB memory, 1 GiB shared buffers,
1 GiB container shared memory, `max_wal_size=4GB`, `min_wal_size=1GB`,
`checkpoint_timeout=15min`, and `checkpoint_completion_target=0.9`.
PostgreSQL can retain close to the configured WAL ceiling and the ceiling is
not a hard limit, so filesystem forecasts must leave room for WAL and archive
failure as well as relation growth.

The host sampler publishes:

- `jobseek_postgresql_database_bytes`;
- timed and requested checkpoint counters;
- cumulative checkpoint write and sync seconds;
- checkpoint buffers and the statistics-reset timestamp;
- duration of the sampler's bounded PostgreSQL statistics query; and
- standard Unix-exporter filesystem size, free-byte, and inode series.

`PostgreSQLDataVolumeHeadroomLow` is the early capacity control. It remains
pending for six hours before firing when either the attached XFS Volume has
less than 25% free or a linear regression over the retained 24-hour database
size projects that database growth alone will consume all current filesystem
headroom within 30 days. The forecast intentionally uses database size rather
than short-window filesystem slope: recycled WAL can move filesystem free
space by several GiB without representing durable data growth.
`PostgreSQLCheckpointPressure` fires only when at least four requested
checkpoints occur within six hours and requested checkpoints outnumber timed
checkpoints. Both route to the daily Codex error review. The fleet-wide
`DiskNearFull` rule remains the last-resort critical control below 10% free.

Read-only live verification:

```bash
docker exec postgres psql -U crawler -d crawler -XAt -F '|' -c \
  "select checkpoints_timed, checkpoints_req, checkpoint_write_time,
          checkpoint_sync_time, buffers_checkpoint, stats_reset
     from pg_stat_bgwriter"
docker exec postgres psql -U crawler -d crawler -XAt -c \
  "select pg_database_size(current_database())"
docker exec postgres psql -U crawler -d crawler -XAt -c \
  "select relname, pg_total_relation_size(relid), n_live_tup, n_dead_tup,
          last_autovacuum
     from pg_stat_user_tables
    order by pg_total_relation_size(relid) desc
    limit 10"
df -h <POSTGRESQL_DATA_MOUNT>
df -i <POSTGRESQL_DATA_MOUNT>
docker exec --user postgres postgres pgbackrest --stanza=jobseek info
```

Use Grafana/Mimir to reproduce the capacity decision without exposing a host
or Volume identifier:

```promql
max(node_filesystem_avail_bytes{
  job="integrations/unix",host_role="postgresql",
  fstype="xfs",mountpoint=~"/mnt/.*"
})
```

```promql
max(predict_linear(
  jobseek_postgresql_database_bytes{host_role="postgresql"}[24h],
  30 * 24 * 60 * 60
)) - max(jobseek_postgresql_database_bytes{host_role="postgresql"})
```

When the capacity rule fires, first distinguish durable database growth from
WAL/archive accumulation and temporary checkpoint recycling. Confirm backup
freshness and archive failures, compare database and top-relation growth, and
check autovacuum progress. Do not run `VACUUM FULL`, delete descriptions, or
offload rows merely to clear an alert: those actions change lock, recovery,
and read-path requirements and need their own measured retention design.
Resize only when the retained growth window and recovery evidence justify the
irreversible change.

When checkpoint pressure fires, compare six-hour counter increases and
checkpoint write/sync time with archive health, WAL directory size, workload
changes, and query-path errors. Occasional requested checkpoints during bulk
work are expected; sustained requested dominance is not. Change WAL or
checkpoint settings only through both repo-owned container creation paths,
with a fresh backup and the guarded rollback workflow. Do not force a
checkpoint or increase `max_wal_size` merely to make the alert disappear.

## Fleet Observability

All three hosts run the same repo-owned host telemetry surface:

- `jobseek-alloy.service` runs Alloy 1.18.0 as the dedicated unprivileged
  `jobseek-alloy` user. The binary is extracted from the checksum-pinned
  official container image during deployment; no mutable `latest` tag or
  package repository is trusted at runtime.
- `jobseek-host-observability.timer` runs the root-owned read-only sampler
  every minute. Root is required only for Docker inspect/log access and local
  PostgreSQL statistics. The sampler cannot reach non-loopback IP addresses,
  performs no Docker or database mutations, and atomically writes a
  world-readable Prometheus textfile containing no credentials or row data.
- Alloy listens only on `127.0.0.1:12347`, reads that textfile plus host
  CPU/RAM/load/swap/filesystem/inode/kernel/network metrics, and remote-writes
  directly to Grafana Cloud. The explicit Unix-exporter collector allowlist
  includes `textfile`; the textfile block alone does not enable that collector.
  No host opens a scrape port.
- The sampler forwards at most 200 new error-class lines per interval from the
  PostgreSQL and Typesense containers into its own journal after redacting
  credentials, URL queries, addresses, UUIDs, and email addresses. Alloy reads
  only the allowlisted Jobseek backup/telemetry/Codex units and `cloudflared`;
  it never receives Docker-socket access.

The crawler Compose Alloy remains responsible for crawler application/Redis
metrics and Docker logs. It is pinned to the same digest, has no privileged or
host-PID mode, and no longer duplicates host metrics. Its read-only Docker
socket remains a privileged trust boundary and is therefore unavailable to
the host collector and to `codex-runner`.

Compose Alloy runs as explicit UID/GID `0:0` with all Linux capabilities
dropped, a read-only root filesystem, and `no-new-privileges`. The deploy
normalizes its persistent WAL/cursor volume to root-owned mode `0700` using a
networkless helper from the same pinned image; this lets the capability-free
process write only that mounted volume and access the root-owned Docker socket.
Deploy success requires the Compose listener at `127.0.0.1:12346` to answer
`/-/ready`, so a merely restart-looping container cannot pass the rollout.

Stable labels deliberately describe roles rather than provider identifiers:

| Role | `instance` | `host_role` |
|---|---|---|
| Crawler | `jobseek-crawler-browser` | `crawler` |
| PostgreSQL | `jobseek-postgresql` | `postgresql` |
| Typesense | `jobseek-typesense` | `typesense` |

The sampler covers container running/restart/OOM state, required systemd
units, reboot-required state, backup attempt/success/freshness, PostgreSQL
readiness/connections/WAL archive/checkpoint duration and dominance/database
size and 30-day capacity forecast/shared-memory capacity, durable cross-store
reconciliation state, and Typesense health/tunnel state. Sticky Docker OOM
flags and absolute restart counters are evidence only; the daily error review
applies generation/time-window rules before declaring a new incident.

Deployment is owned by
[`deploy-hetzner-observability.yml`](../.github/workflows/deploy-hetzner-observability.yml).
It validates the Python, shell, Alloy, alert, and systemd contracts; deploys
the crawler, PostgreSQL, and Typesense hosts sequentially; then polls Grafana
until fresh sampler, probe, container, backup, PostgreSQL-readiness, and
Typesense-readiness textfile series are present and healthy for every expected
role. Only after that ingestion gate passes does it transactionally sync the
three Mimir rule groups. This catches a healthy local sampler whose collector
silently omits the textfile directory. Environment-scoped host variables are
resolved inside runtime steps after the protected `production` environment is
attached. The installer snapshots the prior binary, configuration, secret env,
and units under the root-only
`/var/lib/jobseek-observability/rollback/` directory and automatically
restores them if validation, service startup, or loopback readiness fails;
artifacts that did not exist before the attempt are removed rather than left
as a partial installation.
It restarts only Alloy; it does not restart Docker, PostgreSQL, Typesense, the
tunnel, or any crawler workload.

The config and textfile parent directories are `root:jobseek-alloy` with mode
`0750`; the Alloy config is group-readable, while credential env files,
sampler state, and rollback snapshots remain root-only. The host listener uses
port `12347`, distinct from the crawler Compose Alloy listener on `12346`.
Deployment readiness requires both the loopback endpoint and an active systemd
main PID whose executable is `/usr/local/bin/jobseek-alloy`, so an unrelated
listener cannot make a failed service appear healthy.

Alert definitions in [`apps/crawler/alerts.yaml`](../apps/crawler/alerts.yaml)
are transactionally written through the Mimir ruler API. Grafana Cloud limits
this tenant to 20 rules per group, so the source separates fleet, PostgreSQL
capacity, and crawler alerts into three logical groups at or below that limit.
The sync client first
captures the complete owned namespace, requires every alert to have a
repository runbook plus `owner=codex-error-review` and `route=codex-daily`,
verifies the exact active group/rule set, removes stale owned groups, and
restores the whole prior namespace on failure. This corrects the exporter
alert by selecting only `instance="exporter"` and adds explicit all-host,
disk/inode, sampler, backup, PostgreSQL, Typesense/tunnel, and reboot alerts.
It also routes failed, stale, unresolved, and stuck cross-store reconciliation
state from PostgreSQL-host metrics; reconciliation state does not depend on an
ephemeral crawler process exposing Prometheus.
The intended notification route is the daily Hetzner Codex error-review issue
workflow; alert state is not routed to phone or email.

Check one host without printing configuration or credentials:

```bash
systemctl is-enabled jobseek-alloy.service jobseek-host-observability.timer
systemctl is-active jobseek-alloy.service jobseek-host-observability.timer
systemctl list-timers --all jobseek-host-observability.timer --no-pager
systemctl status jobseek-host-observability.service --no-pager
curl --fail --silent http://127.0.0.1:12347/-/ready
ss -ltnp | grep '127.0.0.1:12347'
grep -v '^#' /var/lib/jobseek-observability/textfile/jobseek-host.prom
journalctl -u jobseek-alloy.service -u jobseek-host-observability.service \
  --since '30 minutes ago' --no-pager
```

Healthy production has one current `up{job="integrations/unix"}` and one
current `up{job="jobseek-alloy"}` series for each stable instance, fresh
`jobseek_host_observability_last_collect_unixtime`, all required probes equal
to one, current backup success timestamps on the two data hosts, PostgreSQL
ready with no new archive failure, and Typesense plus `cloudflared` healthy.
The deployment workflow verifies those custom series after every host rollout;
local timer success and Unix-exporter `up` alone are insufficient evidence.
Treat missing host/sampler series, disk or inode exhaustion, a failed/stale
backup, PostgreSQL archive/readiness failure, or Typesense/tunnel failure as an
incident. Inspect evidence first; this telemetry path does not authorize an
automatic workload restart.

## Cross-store Reconciliation Timer

`jobseek-crawler-reconciliation.timer` is a Hetzner crawler-host systemd timer,
not a GitHub cron and not an in-process exporter loop. Its first start is 20
minutes after the timer is activated; it launches
`/usr/local/sbin/jobseek-crawler-reconciliation` as the unprivileged `deploy`
user. The wrapper resolves the immutable image tag already deployed in
`/home/deploy/.env` and starts a read-only one-shot container with a 1 GiB
memory limit, one CPU, a PID cap, and no persistent container filesystem. It
processes at most 16 partitions per target and then exits. Lock acquisition
may wait up to two hours for an authorized deploy/backfill; once acquired, the
container has a separate 50-minute hard runtime cap. The next timer interval
starts only after this service is inactive, preventing delayed work from
causing an immediate second run. The wrapper filters the crawler environment
into a mode-`0600` ephemeral file containing only the two database URLs and
four Typesense settings; proxy, R2, Redis, Codex, Murmur, and other unrelated
credentials never enter the one-shot container, and the file is removed on
every exit path. It invokes the installed `/app/.venv/bin/crawler` entry point
directly so the read-only root filesystem never depends on a runtime package
manager cache.

At the runtime cap, the crawler observes the wrapper's `SIGTERM`, cancels the
in-flight one-shot task, and persists the run as `interrupted` before container
cleanup. The partition cursor advances only after downstream verification, so
the next invocation retries an interrupted partition. Because a new invocation
holds the global reconciliation advisory lock before creating its ledger row,
it also marks any older `running` rows as interrupted immediately; a prior
row cannot represent a still-live reconciler once that lock has been acquired.

The wrapper holds `/run/lock/jobseek-crawler-mutation.lock` for the whole run.
Crawler deploys, scheduled Typesense refreshes/backfills, and reconciliation
all take that same lock, while PostgreSQL additionally enforces a dedicated
reconciliation advisory lock. This prevents a timer from starting on an old
image during a deploy and prevents Typesense maintenance overlap. The existing
exporter/operator fence serializes each direct repair with cursor advancement;
no crawler, PostgreSQL, or Typesense service is restarted.

Repository-owned deployment is
[`deploy-crawler-reconciliation.yml`](../.github/workflows/deploy-crawler-reconciliation.yml).
It validates and installs the wrapper plus service/timer transactionally as
root, but never starts the reconciliation service directly. A failed install
restores the previous files. The timer is enabled immediately and normally
starts after the boot/cadence delay; the application deploy owns Alembic and
the additive Typesense schema patch.

Read-only health and aggregate evidence:

```bash
systemctl is-enabled jobseek-crawler-reconciliation.timer
systemctl is-active jobseek-crawler-reconciliation.timer
systemctl list-timers --all jobseek-crawler-reconciliation.timer --no-pager
systemctl status jobseek-crawler-reconciliation.service --no-pager
journalctl -u jobseek-crawler-reconciliation.service --since '24 hours ago' --no-pager
docker ps --filter name=jobseek-cross-store-reconciliation --no-trunc
```

Do not print `/home/deploy/.env`, database rows, or Typesense documents during
triage. The PostgreSQL-host textfile exposes only aggregate
`jobseek_cross_store_reconciliation_*` series. Healthy production has both
targets completing a full verified cycle within 30 hours, zero unresolved
drift, no run older than two hours still marked running, and Typesense
bootstrap complete.

To retry the normal bounded repair after correcting a downstream outage:

```bash
systemctl start jobseek-crawler-reconciliation.service
systemctl show jobseek-crawler-reconciliation.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus
journalctl -u jobseek-crawler-reconciliation.service -n 120 --no-pager
```

The database cursor intentionally remains on a failed partition. Never update
`cross_store_reconciliation_state`, delete a run row, or force the Typesense
bootstrap flag to bypass a failure. For an authorized initial/full repair,
first confirm the timer service is inactive, then use the wrapper's validated
operator mode. It retains the same host lock, immutable deployed image,
resource limits, secret filter, and 50-minute cap:

```bash
systemctl is-active jobseek-crawler-reconciliation.service || true
sudo -u deploy /usr/local/sbin/jobseek-crawler-reconciliation \
  --full-target supabase
sudo -u deploy /usr/local/sbin/jobseek-crawler-reconciliation \
  --full-target typesense
```

Run one target at a time and inspect its aggregate result before continuing.
If the cap is reached, the verified partition cursor remains resumable; rerun
the same command rather than increasing limits during an incident. Confirm the
interrupted run is recorded and that the next invocation resumes at the last
verified cursor. Stopping or disabling the timer is a scheduling rollback
only—the migration and optional Typesense bucket field are additive, and
disabling the timer does not undo already verified downstream repairs.

## Disk Triage

Use these first when a deploy fails with `No space left on device`, Redis
reports `MISCONF`, or the `DiskNearFull` alert fires.

```bash
df -hT / /var/lib/docker /var/lib/containerd /var/log 2>/dev/null || true
df -ih /
du -xhd1 /var 2>/dev/null | sort -h | tail -30
du -xhd1 /var/lib 2>/dev/null | sort -h | tail -30
docker system df
docker system df -v | sed -n '1,/^Containers space usage:/p'
journalctl --disk-usage
```

On the crawler host, a common failure mode is accumulated versioned crawler
images under Docker's containerd snapshotter. The visible symptom is
`/var/lib/containerd` dominating `/var/lib`, while `docker system df -v` shows
many unused `ghcr.io/colophon-group/jobseek-crawler:v...` and
`ghcr.io/colophon-group/jobseek-crawler-browser:v...` images.

## Docker GC Timer

All Hetzner hosts should have this host-level timer installed:

- Service: `jobseek-docker-gc.service`
- Timer: `jobseek-docker-gc.timer`
- Script: `/usr/local/sbin/jobseek-docker-gc`
- Cadence: hourly, with a small randomized delay

Check it:

```bash
systemctl is-enabled jobseek-docker-gc.timer
systemctl is-active jobseek-docker-gc.timer
systemctl list-timers --all jobseek-docker-gc.timer --no-pager
journalctl -u jobseek-docker-gc.service -n 80 --no-pager
```

Run it manually:

```bash
systemctl start jobseek-docker-gc.service
journalctl -u jobseek-docker-gc.service -n 30 --no-pager
df -h /
docker system df
```

Current policy:

- prune Docker builder cache older than 24 hours
- prune unused Docker images older than 72 hours
- if root free space is below 5 GiB, prune all unused images
- never prune Docker volumes
- on the crawler host, keep running images plus the two newest versioned
  `jobseek-crawler` and `jobseek-crawler-browser` images, then remove older
  unused version tags immediately

The crawler-specific rule matters because repeated versioned deploys can
consume tens of GiB before a normal age-based prune would trigger.

## Unmanaged Resource Hygiene

The scheduled crawler maintenance workflow runs the read-only
[`crawler-host-hygiene.py`](../scripts/crawler-host-hygiene.py) check after
its normal maintenance command. It fails visibly when either of these has
survived for more than 24 hours:

- a running Docker container without a Compose project label
- a transient systemd service that remains `active (exited)`

This catches forgotten debug containers, one-off test commands, and completed
transient units without deleting anything automatically. Run the same check
manually with:

```bash
python3 /tmp/jobseek-crawler-maintenance/<deployed-sha>/crawler-host-hygiene.py
```

Before applying a printed cleanup command, inspect the resource and confirm
that it is not active production work. Removing a stale container uses
`docker rm -f -- <name>`. Stopping an `active (exited)` transient unit with
`systemctl stop <unit>` also lets watcher loops waiting on `is-active` exit;
verify the watcher is gone and run `systemctl reset-failed <unit>` only if a
failed unit state remains.

## Codex Runner Timers

The recurring company-request resolver and daily Codex routines run on the
crawler host as `codex-runner`, outside Docker and outside the production
crawler environment. Deployment templates live in
[`18-codex-automation-deployment.md`](18-codex-automation-deployment.md) and
[`../deploy/systemd/`](../deploy/systemd/).
Host-surface deployment is CI/CD-owned by
[`deploy-codex-runner.yml`](../.github/workflows/deploy-codex-runner.yml).
That workflow updates the checked-out repo and systemd units; it does not run
`codex exec`, select issues, upload labels, or perform error reviews.

The local Codex desktop automation records for these three routines should be
`PAUSED` after Hetzner cutover. They are retained only as local app state, not
as the production scheduler:

- `jobseek-company-request-resolver`
- `jobseek-daily-classifications`
- `jobseek-daily-error-review`

Do not add or restore GitHub Actions that execute these routines. Manual
recovery uses the same local Codex CLI path from a throwaway worktree, with
`CODEX_EXEC_JSONL` set for trace capture.

Check the last CI/CD host deploy:

```bash
gh run list --workflow deploy-codex-runner.yml --branch main --limit 5
```

Manual host deploy, when CI/CD is unavailable, runs as root with the same
script and should not start a timer:

```bash
git -C /srv/jobseek-codex/repo fetch origin main
JOBSEEK_CODEX_EXPECTED_SHA="$(git -C /srv/jobseek-codex/repo rev-parse origin/main)" \
JOBSEEK_CODEX_START_TIMERS=0 \
bash /srv/jobseek-codex/repo/scripts/deploy-codex-runner-host.sh
```

Check the runner isolation:

```bash
id codex-runner
id -nG codex-runner | tr ' ' '\n' | grep -qx docker && echo 'unexpected docker group'
sudo -u codex-runner test ! -r /home/deploy/.env
sudo -u codex-runner test ! -w /var/run/docker.sock
```

Check the timer and latest run:

```bash
systemctl is-enabled jobseek-codex-docker-lifecycle.service
systemctl is-active jobseek-codex-docker-lifecycle.service
systemctl is-enabled jobseek-codex-governor.timer
systemctl is-active jobseek-codex-governor.timer
systemctl is-enabled jobseek-codex-daily-annotations.timer
systemctl is-active jobseek-codex-daily-annotations.timer
systemctl is-enabled jobseek-codex-daily-error-review.timer
systemctl is-active jobseek-codex-daily-error-review.timer
systemctl list-timers --all 'jobseek-codex*' --no-pager
journalctl -u jobseek-codex-governor.service -n 120 --no-pager
journalctl -u jobseek-codex-daily-annotations.service -n 120 --no-pager
journalctl -u jobseek-codex-daily-error-review.service -n 120 --no-pager
journalctl -u jobseek-codex-docker-lifecycle.service -n 20 --no-pager
```

Check that no routine is currently running before maintenance:

```bash
systemctl is-active jobseek-codex-governor.service || true
systemctl is-active jobseek-codex-daily-annotations.service || true
systemctl is-active jobseek-codex-daily-error-review.service || true
sudo -iu codex-runner fuser /srv/jobseek-codex/state/codex-runner.lock || true
```

Check trace-upload auth without printing the token:

```bash
sudo -iu codex-runner bash -lc 'cd /srv/jobseek-codex/repo/apps/crawler && .venv/bin/python - <<'"'"'PY'"'"'
from huggingface_hub.utils import get_token
t = get_token()
print("hf token present", bool(t), "length", len(t or ""))
raise SystemExit(0 if t else 1)
PY'
```

Run one dry-run pass after changing config:

```bash
sudo -iu codex-runner
git config --global user.name "Jobseek Codex Runner"
git config --global user.email "codex-runner@colophon-group.org"
codex login --device-auth
gh auth login
exit

install -o root -g codex-runner -m 0640 \
  deploy/systemd/jobseek-codex-governor.env.example \
  /etc/jobseek-codex/governor.env
sed -i 's/^JOBSEEK_CODEX_DRY_RUN=.*/JOBSEEK_CODEX_DRY_RUN=true/' \
  /etc/jobseek-codex/governor.env
systemctl start jobseek-codex-governor.service
journalctl -u jobseek-codex-governor.service -n 120 --no-pager
```

Check daily routine prerequisites without printing secrets:

```bash
sudo -iu codex-runner test -s /home/codex-runner/.codex/auth.json
sudo -iu codex-runner gh auth status >/dev/null
sudo -iu codex-runner bash -lc 'cd /srv/jobseek-codex/repo/apps/crawler && .venv/bin/python - <<'"'"'PY'"'"'
from huggingface_hub.utils import get_token
raise SystemExit(0 if get_token() else 1)
PY'
test -s /etc/jobseek-codex/labeller.env
sudo -u codex-runner test -r /etc/jobseek-codex/labeller.env
sudo -u codex-runner test ! -w /var/run/docker.sock
```

Smoke-test the read-only annotation database role without printing the DSN:

```bash
sudo -iu codex-runner bash -lc 'set -a; . /etc/jobseek-codex/labeller.env; set +a; cd /srv/jobseek-codex/repo/apps/crawler && .venv/bin/python - <<'"'"'PY'"'"'
import asyncio
import os
import asyncpg

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        value = await conn.fetchval("SELECT count(*) FROM job_posting")
        print("job_posting count readable", value is not None)
    finally:
        await conn.close()

asyncio.run(main())
PY'
```

Check the root-collected error-review evidence bundle:

```bash
test -s /srv/jobseek-codex/inputs/error-review/latest/manifest.json
sudo -u codex-runner test -r /srv/jobseek-codex/inputs/error-review/latest/manifest.json
find /srv/jobseek-codex/inputs/error-review/latest -maxdepth 1 -type f -printf '%f\n' | sort
```

The ChatGPT usage probe is advisory only. A failed probe should be visible in
the governor ledger or journal, but it should not permanently fail the timer:

```bash
sudo -u codex-runner python3 /srv/jobseek-codex/repo/scripts/codex-usage-probe.py \
  --auth-file /home/codex-runner/.codex/auth.json \
  --timeout 10
```

Inspect usage-limit depletion history from the governor ledger:

```bash
sudo -iu codex-runner bash -lc 'python3 - <<'"'"'PY'"'"'
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("/srv/jobseek-codex/state/ledger.sqlite")
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT observed_at, window_name, remaining_percent, used_percent,
           reset_in_seconds, decision_reason, recent_limit, recent_runs,
           pacing_interval_s, retry_after_s, usage_error
    FROM usage_snapshots
    WHERE window_name IN ('weekly', 'five_hour') OR window_name IS NULL
    ORDER BY observed_at DESC, id DESC
    LIMIT 40
""").fetchall()
for row in rows:
    ts = datetime.fromtimestamp(row["observed_at"], tz=timezone.utc).isoformat()
    print(
        ts,
        row["window_name"],
        "remaining=", row["remaining_percent"],
        "used=", row["used_percent"],
        "reset_s=", row["reset_in_seconds"],
        "decision=", row["decision_reason"],
        "cap=", row["recent_limit"],
        "recent=", row["recent_runs"],
        "pace_s=", row["pacing_interval_s"],
        "retry_s=", row["retry_after_s"],
        "error=", row["usage_error"],
    )
PY'
```

Inspect active and recent routine slots in the same ledger:

```bash
sudo -iu codex-runner bash -lc 'python3 - <<'"'"'PY'"'"'
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("/srv/jobseek-codex/state/ledger.sqlite")
conn.row_factory = sqlite3.Row
for table in ("active_slot", "runs"):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        print(table, "missing")
        continue
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 20").fetchall()
    print("==", table, "==")
    for row in rows:
        print(dict(row))
PY'
```

## Safe Manual Image Cleanup

Prefer the GC service above. If the crawler host is already near full and the
timer has not recovered it, manually remove only unused old crawler images.

First identify active and rollback images:

```bash
cd /home/deploy
docker compose ps
grep '^CRAWLER_IMAGE_TAG=' /home/deploy/.env
docker images 'ghcr.io/colophon-group/jobseek-crawler*' \
  --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}} {{.CreatedSince}}'
```

Keep the currently deployed crawler/browser tag and at least one recent
rollback pair. Remove older unused version tags with `docker rmi <image-ref>`.
Docker will reject removal of any image still referenced by a container unless
forced; do not force-remove running deployment images.

After cleanup, verify Redis and workers:

```bash
cd /home/deploy
docker compose ps
docker exec deploy-redis-1 redis-cli INFO persistence \
  | tr -d '\r' \
  | grep -E '^(rdb_bgsave_in_progress|rdb_last_bgsave_status|aof_enabled):'
docker exec deploy-redis-1 redis-cli SET disk_probe ok EX 60
df -h /
docker system df
```

## Redis Disk-Full Recovery

When the crawler host root disk fills, Redis RDB saves can fail and Redis may
reject writes with `MISCONF`. After freeing disk, confirm persistence and
write health:

```bash
docker exec deploy-redis-1 redis-cli BGSAVE
docker exec deploy-redis-1 redis-cli INFO persistence \
  | tr -d '\r' \
  | grep -E '^(rdb_last_bgsave_status|rdb_bgsave_in_progress):'
docker exec deploy-redis-1 redis-cli SET redis_write_probe ok EX 60
```

Then sample worker logs for claim failures:

```bash
for c in deploy-worker-1-1 deploy-worker-2-1 deploy-worker-3-1 deploy-browser-1-1; do
  echo "$c"
  docker logs "$c" --since 20m 2>&1 | grep -c 'pipeline.claim_error' || true
done
```

## Disk Resize

Hetzner primary disks grow by rescaling the server. Take a snapshot first,
rescale in Hetzner Console, and do not choose the keep-disk option if the goal
is larger disk.

After resize and reboot, if the guest did not auto-grow the filesystem:

```bash
lsblk
growpart /dev/sda 1
resize2fs /dev/sda1
df -h /
```

The PostgreSQL data filesystem is different: it is XFS on an attached Hetzner
Volume, and server snapshots do not contain it. Verify the encrypted off-host
logical checkpoint first, then expand the Volume in the Hetzner control plane
and grow XFS online:

```bash
findmnt /mnt/HC_Volume_105256309
lsblk -f
xfs_growfs /mnt/HC_Volume_105256309
df -h /mnt/HC_Volume_105256309
```

An attached Volume expansion cannot be reversed in place. Record the old and
new sizes and verify PostgreSQL before proceeding. Do not substitute a server
backup for the pre-change data checkpoint; follow
[`19-data-backup-recovery.md`](19-data-backup-recovery.md).

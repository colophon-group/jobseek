# Hetzner Maintenance

Operational runbook for the Jobseek Hetzner machines.

## Machines

Credentials and current IPs live in `apps/crawler/.env.local`; do not hardcode
secrets into commands or documentation.

| Role | Host variable | Main workload |
|------|---------------|---------------|
| Crawler | `CRAWLER_BROWSER_IPv4` | Workers, browser worker, exporter, drain, Redis, Alloy, murmur shim |
| Postgres | `POSTGRESQL_LOCAL_IPv4` | Local crawler Postgres |
| Typesense | `TYPESENSE_IPv4` | Typesense, `cloudflared`, and the encrypted web PostgreSQL logical backup job |

Crawler PostgreSQL, Typesense, and web PostgreSQL data protection is documented separately in
[`19-data-backup-recovery.md`](19-data-backup-recovery.md). That runbook is
the source of truth for backup scheduling, validation, restore drills, and
the removal gate for legacy server backups.

## Provider Lifecycle Baseline

[`manage-hetzner-fleet.py`](../scripts/manage-hetzner-fleet.py) is the
fail-closed source of truth for the provider-side ownership labels and
lifecycle protection of the Jobseek production fleet. Its allowlist is fixed
in code: the `jobseek-crawler`, `jobseek-postings-postgresql`,
`jobseek-typesense`, and `murmur-server` servers; the
`jobseek-postings-postgresql`, `jobseek-typesense-snapshots`, and
`murmur-volume` Volumes; and `jobseek-network`. The command does not accept
names or provider IDs as arguments, does not use `hcloud` or `jq`, and does not
change placement, server backups, snapshots, or any application backup
schedule.

The desired labels are `environment=production`, `project=jobseek`,
`owner=jobseek-operations`, and the resource's fixed `role`. Label updates
merge these four keys into the current map instead of replacing unrelated
labels. All four servers require both delete and rebuild protection. All three
Volumes and the private network require delete protection. Volume roles follow
the owning service: `postgresql`, `typesense`, and `murmur` respectively.

Load `HETZNER_API_KEY` from the root-only operator environment and run the
default read-only check first:

```bash
python3 scripts/manage-hetzner-fleet.py
```

Exit `0` means the complete allowlist is conformant, `2` means the dry-run
found drift, and `1` means inventory or API state could not be proved. Output
is intentionally limited to allowlisted names, booleans, and planned action
types; it omits provider IDs, addresses, tokens, unrelated label values, and
API response bodies. Missing resources, duplicate matches, malformed
protection state, missing or inconsistent single-page metadata, or a
non-unique inventory stops the whole run before the first mutation. The HTTP
transport rejects redirects, response or request bodies above 1 MiB, and every
endpoint outside exact-name reads, exact-ID reads, label updates, protection
actions, and bounded action-status polling. It cannot issue delete, rebuild,
reboot, resize, power, snapshot, backup, or network-topology requests.

After reviewing the complete dry-run, enable protection and merge the managed
labels with the explicit apply flag:

```bash
python3 scripts/manage-hetzner-fleet.py --apply
```

Apply enables and verifies all required deletion/rebuild protections before
it starts label updates. It polls each provider protection action at most 60
times with one-second spacing; each HTTPS request also has a 30-second timeout.
It stops on the first failed, timed-out, mismatched, or unprovable action and
never rolls back a successfully enabled protection.

Hetzner label writes replace the entire label map; the provider has no
conditional label-update operation. Before each write, the tool therefore
reads the exact provider ID twice, rejects a replaced identity or observed
concurrent label change, merges the managed keys into that stable map, and
proves that every observed label survived both the response and an immediate
re-read. Operators must still serialize `--apply` with other Hetzner label
writers. After all mutations, the tool resolves all eight names again and
returns success only when every managed label and protection field matches. A
partially completed run is safe to repeat: it sends only still-needed changes
and never weakens protection. Retain the successful redacted JSON as rollout
evidence. This repository change does not itself apply the baseline to
production.

Protection rollback is deliberately outside this tool. Do not disable delete
or rebuild protection to troubleshoot an application incident. If an approved
provider operation truly requires it, record the exact resource in a private
change ticket, disable only the required field in the Hetzner control plane,
perform the bounded operation, and immediately rerun `--apply`. If managed
label values must be restored, capture their pre-apply values in a root-only
artifact (never a CI log), restore only those four keys while preserving the
rest of the label map, then document why the repository baseline must change
before the next apply.

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
The Snapshot API writes directly through `/jobseek-snapshots` to the dedicated
host filesystem mounted at `/mnt/jobseek-typesense-backup`; the backup job
never makes a second `docker cp` copy on `/`. On the reviewed 8 GB CX33 host,
the managed container has a 6 GiB hard memory limit, 5 GiB reservation, and a
6 GiB memory-plus-swap ceiling, so swap cannot silently extend its resource
envelope and roughly 2 GB remains for the OS, Docker, and Cloudflare Tunnel.
The installer checks `/proc/meminfo` before acquiring locks or changing host
state and refuses this policy below 7 GiB total memory. Durable current, peak,
event, Docker, Cloudflare tunnel, and OS headroom measurements still gate the
seven-day acceptance window.

The CX33 transition temporarily recognizes the exact legacy 3 GiB / 2.5 GiB /
3 GiB tuple in the host verifier and backup runner so the pre-cutover service
remains inspectable and the post-cutover runner can be installed without a
dead zone. The protected host workflow explicitly selects `expanded`, while
the host installer retains an explicit `legacy` rollback mode and the backup
installer requires 6 GiB / 5 GiB / 6 GiB for the new contract. Remove legacy
recognition in [#8059](https://github.com/colophon-group/jobseek/issues/8059)
immediately after the expanded host, fresh backup smoke, and capacity
acceptance checks pass; it is a migration bridge, not a second steady-state
policy.

Provision the snapshot filesystem before promoting the container contract.
Create and attach a dedicated Hetzner Volume of at least 20 GiB in the
Typesense server's location. Resolve the exact new device through
`/dev/disk/by-id`, prove that it is the intended empty Volume with `lsblk` and
`blkid`, and format it only when it has no filesystem. Mount its filesystem by
UUID at `/mnt/jobseek-typesense-backup` with `nodev,nosuid,noexec`, then set the
mount root to `root:root` mode `0700`. Never format a device that already has a
filesystem or is mounted at `/mnt/typesense-data`.

The promotion preflight is fail-closed:

```bash
findmnt --mountpoint=/mnt/jobseek-typesense-backup
stat -c 'owner=%U:%G mode=%a device=%d' /mnt/jobseek-typesense-backup
stat -c 'root_device=%d' /
stat -c 'live_device=%d' /mnt/typesense-data
df -B1 --output=size,used,avail /mnt/jobseek-typesense-backup
```

The staging device must differ from both printed devices. Persist exactly one
`/etc/fstab` entry with a `UUID=` source and `nodev,nosuid,noexec`; the live
mount source, filesystem type, and safety options must match it. Its capacity
must be at least 20 GiB. Before a snapshot, available bytes must cover the
current allocated live-data bytes plus a 4 GiB growth reserve and the protected
8 GiB floor; after materialization, the 4 GiB growth reserve plus 8 GiB floor
must remain. Both installers call the shared fail-closed verifier and install
it under `/usr/local/sbin` for the reboot/rollback checks below; the hardened
backup unit requires the mount point, and the backup code repeats the capacity
checks around every snapshot.

Before promotion, exercise persistence without rebooting the production
service:

```bash
python3 scripts/verify-typesense-snapshot-mount.py
systemctl stop jobseek-typesense-backup.timer jobseek-typesense-backup.service
umount /mnt/jobseek-typesense-backup
mount -a
python3 scripts/verify-typesense-snapshot-mount.py
```

Do this before the container receives the bind mount. A later reboot check must
show the same UUID-backed source before the backup timer is enabled.
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
and set all four protected secrets first. Merge the reviewed revision, then use
this exact staged order. Production backup deployments are manual so a merge
cannot race the manual host contract:

```bash
gh workflow run deploy-typesense-host.yml --ref main -f component=typesense
gh run list --workflow deploy-typesense-host.yml --commit "$(git rev-parse main)" \
  --event workflow_dispatch --limit 5
gh run watch <HOST_TYPESENSE_RUN_ID> --exit-status

gh workflow run deploy-data-backups.yml --ref main -f service=typesense
gh run list --workflow deploy-data-backups.yml --commit "$(git rev-parse main)" \
  --event workflow_dispatch --limit 5
gh run watch <TYPESENSE_BACKUP_RUN_ID> --exit-status

gh workflow run deploy-typesense-host.yml --ref main -f component=cloudflared
gh run list --workflow deploy-typesense-host.yml --commit "$(git rev-parse main)" \
  --event workflow_dispatch --limit 5
gh run watch <CLOUDFLARED_RUN_ID> --exit-status
```

Select the just-created run ID whose head SHA is the printed main SHA; do not
dispatch the next step until `gh run watch --exit-status` succeeds. The host
and backup workflows also share host locks, but those locks are a final race
barrier, not a substitute for waiting on each acceptance gate.

For the CX23-to-CX33 capacity change, keep the
`deployment-hold:crawler` label in place and obtain explicit operator approval
for the exact +€3/month resize before mutating Hetzner. Gracefully stop the
server, change only its type while retaining the disk, start it, and verify the
host reports at least 7 GiB before dispatching the reviewed Typesense host
revision. The merged memory policy does not deploy automatically. After the
manual dispatch, require local and public health, exact 6 GiB / 5 GiB / 6 GiB
Docker limits, all seven aliases, a full reconciliation/rebuild acceptance
check, and measured OS headroom before removing the deployment hold. If any
gate fails, leave the hold active and return to the prior reviewed host
contract or resize plan; do not weaken the memory preflight.

Typesense and cloudflared record separate deployed-revision markers. A
cloudflared-only reconciliation never rewrites the Typesense SHA used by the
pending backup contract, so it cannot invalidate or race a staged snapshot.

The Typesense host step acquires the shared deployment and service-data locks,
then disables the old backup timer before changing the container and leaves a
revision-bound `backup-contract-pending` marker. The
backup deployment refuses a different Git SHA or a container without the exact
direct-mount label, writable source, persistent filesystem, and exact bounded
memory policy. It installs the new contract, runs a fresh full snapshot even
when the backup key is unchanged, validates the post-copy floor and status
fields, then starts the timer and removes the pending marker. Any failed gate
leaves the timer disabled. Do not dispatch the backup step first or dispatch
the two workflows from different revisions.

Rollback is staged too. A host transaction that cannot reach readiness
restores the prior container/config and exact prior timer state. After the host
step has succeeded, keep the timer disabled and prefer fixing the backup gate
forward. If the container contract itself must be reverted, dispatch the prior
reviewed host SHA, verify local/public health, and do not re-enable its legacy
root-staging backup. Restore a compatible reviewed backup contract manually
before clearing the pending marker. Detach or remove the Volume only after the
timer and service are disabled, no container mounts it, `umount` succeeds, and
its UUID entry has been removed from `/etc/fstab`. On the next reboot, require
`findmnt --mountpoint=/mnt/jobseek-typesense-backup` plus the shared verifier
before enabling the timer; a missing mount intentionally leaves the service
condition unsatisfied.

The Typesense step requires independently retained last-success backup
evidence no older than 36 hours and refuses to run while
`jobseek-typesense-backup.service` is active. A failed latest attempt does not
deadlock this corrective host-first rollout when that last-success timestamp
is still fresh; it remains an outage signal, and the timer stays disabled until
the new direct-mount smoke succeeds. The step
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

For a backup-key rotation, update `TYPESENSE_BACKUP_KEY`, dispatch the
Typesense host component to stage the exact main SHA, then dispatch
`deploy-data-backups.yml` with `service=typesense` at that SHA. The backup
installer probes the candidate against `/stats.json` without changing the live
environment. It quiesces the timer/service and runs a full backup smoke from a
root-only candidate environment even when the key bytes are unchanged,
retaining the host-wide deployment lock. It reacquires the service-data lock
and atomically commits only a changed candidate. Rollback stays armed until
fresh status, timer health, and the deployment marker have committed. Any
failure restores the prior value and leaves the timer disabled/inactive; treat
a reported rollback failure as a hard backup outage. Delete the superseded
generated key only after that backup and the isolated restore drill in
`19-data-backup-recovery.md` both pass.

Read-only, redacted verification:

```bash
/usr/local/sbin/jobseek-verify-typesense-host-credentials
systemctl is-active cloudflared.service
docker inspect typesense --format \
  'running={{.State.Running}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} memory={{.HostConfig.Memory}} reservation={{.HostConfig.MemoryReservation}} swap={{.HostConfig.MemorySwap}} labels={{json .Config.Labels}} mounts={{json .Mounts}} cmd={{json .Config.Cmd}}'
curl --fail --silent http://127.0.0.1:8108/health
curl --fail --silent https://typesense.colophon-group.org/health
```

Do not manually recreate Typesense with `--api-key`, put a token directly in
`ExecStart`, or print either root-only credential file. Use a component
dispatch for recovery; its transaction and conformance checks are the
supported restart path.

## Typesense Readiness and Raft Recovery

Container uptime is not service readiness. `jobseek_typesense_healthy` is the
authoritative local readiness signal; the public tunnel health is a separate
check. The host sampler also exports:

- `jobseek_typesense_open_file_descriptors` and the live soft/hard limits;
- `jobseek_typesense_threads`;
- the maximum thread-pool queue and slow-request duration seen in the last
  five minutes; and
- bounded five-minute event counts for descriptor exhaustion, leaderlessness,
  snapshot failure, slow requests, and thread-pool exhaustion.

The managed container requires a 65,536 soft/hard `nofile` limit, rotates
Docker JSON logs at 50 MB with three files, and enforces the exact 6 GiB hard
limit / 5 GiB reservation / 6 GiB memory-plus-swap tuple plus the labelled
writable snapshot mount. Deployment conformance verifies all Docker metadata
and the effective process limit. Allow up to 15 minutes for the current
2.5-million-document index to reload before declaring a cold start failed.

After promotion, retain seven consecutive days before accepting the capacity
remediation. Record the exact UTC start/end and deployed SHA in the issue
ledger, attach the Grafana query results, the Loki result, and the seven fresh
backup status timestamps. These PromQL gates must all pass for the full window
(`$typesense` means the Typesense host labels):

```promql
min_over_time(jobseek_backup_last_attempt_success{service="typesense"}[7d]) == 1
changes(jobseek_backup_last_success_unixtime{service="typesense"}[7d]) >= 6
min_over_time(jobseek_typesense_backup_staging_isolated{service="typesense"}[7d]) == 1
max_over_time(jobseek_typesense_backup_peak_local_copies{service="typesense"}[7d]) <= 1
min_over_time(jobseek_typesense_snapshot_staging_available_bytes[7d]) >= 12884901888
max_over_time(jobseek_typesense_snapshot_local_copies[7d]) <= 1
min_over_time(jobseek_typesense_snapshot_mount_available[7d]) == 1
min_over_time((jobseek_typesense_backup_staging_available_bytes_before{service="typesense"} - jobseek_typesense_backup_staging_required_bytes_before{service="typesense"})[7d:]) >= 0
min_over_time((jobseek_typesense_backup_staging_available_bytes_after_snapshot{service="typesense"} - jobseek_typesense_backup_staging_required_bytes_after_snapshot{service="typesense"})[7d:]) >= 0
max_over_time(jobseek_typesense_backup_local_copies_before{service="typesense"}[7d]) == 0
min_over_time(jobseek_typesense_backup_local_copies_after_materialization{service="typesense"}[7d]) == 1
min_over_time(jobseek_typesense_backup_memory_limit_enforced{service="typesense"}[7d]) == 1
min_over_time(jobseek_typesense_backup_memory_policy_info{service="typesense",phase="enforced"}[7d]) == 1
min_over_time(jobseek_typesense_backup_memory_limit_bytes{service="typesense"}[7d]) == 6442450944
min_over_time(jobseek_typesense_backup_memory_reservation_bytes{service="typesense"}[7d]) == 5368709120
min_over_time(jobseek_typesense_backup_memory_swap_limit_bytes{service="typesense"}[7d]) == 6442450944
max_over_time(jobseek_container_oom_killed{container="typesense"}[7d]) == 0
increase(jobseek_container_restart_count{container="typesense"}[7d]) == 0
increase(jobseek_container_memory_events_total{container="typesense",event=~"oom|oom_kill|oom_group_kill"}[7d]) == 0
min_over_time(jobseek_container_memory_observation_available{container="typesense"}[7d]) == 1
max_over_time(jobseek_container_memory_current_bytes{container="typesense"}[7d])
max_over_time(jobseek_container_memory_peak_bytes{container="typesense"}[7d])
min_over_time(node_memory_MemAvailable_bytes{host_role="typesense"}[7d])
max_over_time(jobseek_host_unit_memory_peak_bytes{host_role="typesense",unit=~"docker.service|cloudflared.service"}[7d])
min_over_time(jobseek_host_unit_memory_observation_available{host_role="typesense",unit=~"docker.service|cloudflared.service"}[7d]) == 1
min_over_time(jobseek_typesense_healthy[7d]) == 1
```

The final four memory values verify that the bounded policy remains healthy;
the ledger must state the observed maximum and remaining host headroom. The
central Loki query below must return no emergency all-unused-image run. The
Alloy allowlist explicitly retains this unit; do not use a local journal as the
only seven-day artifact.

```logql
{host_role="typesense",unit="jobseek-docker-gc.service"}
  |~ "(?i)(emergency|below.?5.?GiB|all unused image)"
```

Also verify the public tunnel and all seven aliases once per day. Any failed or
missing observation resets the seven-day window. A later reviewed issue may
resize the host or adjust the policy only from this durable evidence. The
direct staging mount remains in place regardless of that decision.

When readiness fails but the container is running:

1. Preserve `docker inspect`, `/proc/<pid>/limits`, filesystem/inode/memory
   state, `/health`, `/debug`, and logs covering the first failure. Store them
   in a root-only incident directory. Never print API keys or full slow-query
   URLs.
2. Check the causal signals in order: thread-pool queue, slow requests,
   descriptor use/exhaustion, snapshot failure, then leaderlessness. Stopping
   a downstream writer does not repair an already leaderless Raft node.
3. Verify no Typesense backup is active. Before any peer reset or data-file
   change, gracefully stop Typesense and make an exact offline copy of
   `/mnt/typesense-data`; record byte and file counts. Preserve the old
   container by renaming it rather than deleting it.
4. Attempt a normal start of the same reviewed image/data first. A healthy
   single node loads its snapshot, replays the remaining log, elects itself,
   and reports Raft state `1`. HTTP 503 is expected while the in-memory index
   reloads.
5. Use Typesense's `--reset-peers-on-error` only if the fully loaded normal
   start returns to persistent Raft `ERROR`, and only after the offline copy.
   The [versioned Typesense documentation](https://typesense.org/docs/27.1/api/server-configuration.html#clustering)
   warns that forced peer reset can cause intermittent data loss. Remove the
   flag after the one recovery start.

Recovery acceptance requires all of the following:

```bash
curl --fail --silent http://127.0.0.1:8108/health
curl --fail --silent https://typesense.colophon-group.org/health
docker inspect typesense --format \
  'running={{.State.Running}} restarts={{.RestartCount}} ulimits={{json .HostConfig.Ulimits}}'
pid="$(docker inspect typesense --format '{{.State.Pid}}')"
grep '^Max open files' "/proc/$pid/limits"
```

From the crawler's protected operations environment, also require `/debug`
state `1`, all seven aliases, representative posting/company/watchlist reads,
an ephemeral create/write/read/delete probe, exporter progress, and a complete
Typesense reconciliation cycle. Preserve the offline copy until the linked
application-consistent backup and isolated restore have passed. The 2026-08-03
drill and capacity analysis are in
[`docs/audits/2026-08-03-typesense-raft-incident.md`](audits/2026-08-03-typesense-raft-incident.md).

## Ingress and SSH Baseline

The repository-owned ingress source of truth is:

- [`manage-hetzner-ingress.py`](../scripts/manage-hetzner-ingress.py) for the
  Hetzner Cloud Firewall attached to the three non-Murmur production servers;
- [`install-host.sh`](../deploy/networking/install-host.sh) for UFW and sshd;
- [`harden-postgresql.sh`](../deploy/networking/harden-postgresql.sh) for the
  PostgreSQL listener and exact HBA;
- [`run-remote.sh`](../deploy/networking/run-remote.sh) for the fail-closed,
  host-identity-pinned OpenSSH transport;
- [`jobseek-ingress-conformance.py`](../scripts/jobseek-ingress-conformance.py)
  for redacted host evidence; and
- [`deploy-hetzner-ingress.yml`](../.github/workflows/deploy-hetzner-ingress.yml)
  for protected audit and apply operations.

The protected `production` environment stores `HETZNER_API_TOKEN`,
`HETZNER_HOST`, `HETZNER_POSTGRES_HOST`, `HETZNER_TYPESENSE_HOST`,
`HETZNER_SSH_KEY`, and three reviewed known-host sets as secrets. The crawler
uses `HETZNER_CRAWLER_KNOWN_HOSTS`, PostgreSQL uses the existing
`HETZNER_BACKUP_KNOWN_HOSTS`, and Typesense uses
`HETZNER_TYPESENSE_KNOWN_HOSTS`. Every audit, payload transfer, private-path
validation, rollback, and commit connection uses native OpenSSH with strict
host-key checking and `IdentitiesOnly=yes`; the transport refuses a target
that is absent from its role's exact known-host set. It never learns trust with
`accept-new` or `ssh-keyscan`, never substitutes another role's key set, and
does not use third-party SSH/SCP actions.

Host addresses are secrets for log-redaction purposes even though they are not
authentication material. Do not convert them to GitHub variables: Actions
prints ordinary variables in step environments. The inventory helper emits
GitHub masking commands before exporting derived private addresses;
suppressing that output would disable the masks.

Apply payloads are archived from the reviewed checkout and extracted only into
a fresh `/var/lib/jobseek-ingress/staging/<sha>-<run>-<attempt>` directory.
The transport requires the state root, staging root, and fresh stage to be
non-symlink directories owned by `root:root` with mode `0700`; rejects any
symlink, non-root-owned entry, reused stage, or unexpected file; and verifies
the SHA-256 of every payload before root executes it. No root-executed ingress
payload is staged under `/tmp`.

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

Host commits remain independent only after all three staged policies and the
fresh crawler private paths have passed. If one host's commit fails, that
lane attempts every rollback layer still pending and the provider-firewall job
is withheld; a different lane that already committed keeps its independently
proven-safe host policy. This is an explicit recovery state rather than a
fleet-wide atomic commit. Rerun the read-only audit, resolve the failed lane,
and repeat the guarded apply. Never attach the provider firewall by hand to
bypass the failed workflow.

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
`PostgreSQLSharedMemoryPressure` rule fires and routes to the daily Codex error
review if the configured contract regresses or available capacity remains
below 15% for three minutes.

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
not on the server root disk. It was expanded online from 20 to 40 GiB on
2026-07-22 after a transaction-consistent encrypted checkpoint passed, then
from 40 to 80 GiB during #6117 recovery on 2026-08-03 after the full backup
repository had forced more than 21 GiB of unarchived WAL onto the data Volume.
The second expansion restored crash-recovery workspace; bounded repository
retention fixes the actual growth source. Provider expansion cannot be
reversed in place. Current and future expansion must therefore preserve the
same sequence: fresh backup and restore evidence, recorded pre-change
capacity, provider resize, online `xfs_growfs`, PostgreSQL and archive
verification, then recorded post-change capacity. Never use a server backup as
a substitute; server images do not contain this Volume.

The live PostgreSQL contract is deliberately consistent across
`deploy/backups/postgresql/migrate-container.sh` and
`deploy/networking/harden-postgresql.sh`: 4 GiB memory, 1 GiB shared buffers,
1 GiB container shared memory, `max_wal_size=4GB`, `min_wal_size=1GB`,
`checkpoint_timeout=15min`, and `checkpoint_completion_target=0.9`.
PostgreSQL can retain close to the configured WAL ceiling and the ceiling is
not a hard limit, so filesystem forecasts must leave room for WAL and archive
failure as well as relation growth.

The #6117 recovery baseline measured the durable database at 19.79 GB versus
approximately 19.2 GB on 2026-07-22: less than 0.6 GB growth in twelve days,
or about 1.5 GB over 30 days at the observed linear rate. After archive
catch-up, the 80 GiB Volume therefore retains more than 50 GB for ordinary
headroom after the 2 GiB reserve and the normal WAL ceiling. The 25% current
headroom rule still leaves substantially more than the measured 30-day growth;
do not count the emergency reserve as ordinary free space.

The host sampler publishes:

- `jobseek_postgresql_database_bytes`;
- timed and requested checkpoint counters;
- cumulative checkpoint write and sync seconds;
- checkpoint buffers and the statistics-reset timestamp;
- duration of the sampler's bounded PostgreSQL statistics query; and
- standard Unix-exporter filesystem size, free-byte, and inode series.

PostgreSQL client ownership, exact service/deploy budgets, idle-transaction
controls, and the required seven-day acceptance queries live in
[the PostgreSQL connection budget](22-postgresql-connections.md). Treat that
inventory as part of the host capacity contract; do not raise
`max_connections` to compensate for an unattributed or oversized pool.

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

## PostgreSQL Emergency Headroom

The attached XFS data Volume contains a root-owned, fully allocated 2 GiB file
named `.jobseek-postgresql-emergency-reserve`. It is not normal free capacity:
it is a controlled last-resort reserve that lets crash recovery start and WAL
archiving resume when ordinary filesystem space has been exhausted. The backup
host installer creates and verifies it only when at least 8 GiB remains free
after allocation. A sparse file, symlink, wrong-sized file, or wrong ownership
does not satisfy the contract.

Check it without changing capacity:

```bash
/usr/local/sbin/jobseek-postgresql-emergency-headroom status
```

Release it only after preserving incident evidence and proving that block
exhaustion prevents PostgreSQL recovery:

```bash
/usr/local/sbin/jobseek-postgresql-emergency-headroom release
```

The release command validates the exact file before removing it. It never
touches PostgreSQL data or WAL. Recreate the reserve before declaring recovery
complete:

```bash
/usr/local/sbin/jobseek-postgresql-emergency-headroom reserve
systemctl restart jobseek-postgresql-emergency-headroom.service
```

The host sampler publishes allocated and target bytes;
`PostgreSQLEmergencyHeadroomMissing` fires within five minutes if the reserve
is absent or under-allocated. Crawler deployment and scheduled maintenance run
the Grafana-backed PostgreSQL operational preflight before stopping or
replacing any workload. It requires fresh telemetry, database readiness, at
least 15% XFS free, at least 20% backup-repository free, the full reserve, a
fresh successful backup, and no archive failure in the latest hour.

## Fleet Observability

All three hosts run the same repo-owned host telemetry surface:

- `jobseek-alloy.service` runs Alloy 1.19.2 as the dedicated unprivileged
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
- The sampler also probes the native Alloy listener on all three hosts and the
  crawler Compose Alloy listener. It republishes only a fixed set of readiness,
  memory, queue, send-timestamp, rejection, failure, and dropped-entry values.
  Full Alloy self-scrapes are deliberately prohibited because they previously
  consumed roughly 1,565 active series per host and could disappear at exactly
  the same time as the collector they were meant to monitor.

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
until fresh sampler, probe, container, backup, PostgreSQL-readiness,
Typesense-readiness, and Codex daily-review status series are present and
healthy for every expected role. Only after that ingestion gate passes does it
remove the retired Jobseek notification routes, contact point, bridge,
deadman, and synthetic test rule, followed by the owned Mimir rule groups. This
catches a healthy local sampler whose collector
silently omits the textfile directory. Environment-scoped host variables are
resolved inside runtime steps after the protected `production` environment is
attached. The installer snapshots the prior binary, configuration, secret env,
and units under the root-only
`/var/lib/jobseek-observability/rollback/` directory and automatically
restores them if validation, service startup, or loopback readiness fails;
artifacts that did not exist before the attempt are removed rather than left
as a partial installation. Failed attempts never run retention. After a new
surface is fully accepted and its automatic rollback trap is disarmed, the
installer prunes under the same deployment lock. It keeps at most the three
newest timestamped snapshots, removes snapshots older than 14 days, and always
keeps the just-accepted rollback even when it is the only remaining snapshot.
The complete root is validated before deletion: its path, ownership, mode,
timestamp names, directory types, and allowlisted regular-file contents must
all match the installer contract, with no symlinks. The kernel mount identity
of every snapshot directory and file must also equal the rollback root, so a
bind mount is rejected even when it shares the root filesystem's device ID.
Any unexpected entry stops retention without deleting known snapshots;
operators must classify and move that entry explicitly before rerunning the
reviewed deployment. A retention failure happens after service acceptance, so
it reports deployment failure but does not roll the healthy surface back.
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
capacity, Typesense reliability, telemetry delivery, crawler reliability, and
operator handoff alerts into logical groups at or below that limit.
The sync client first
captures the complete owned namespace, requires every alert to have a
repository runbook plus `owner=codex-error-review` and `route=codex-daily`,
and additionally rejects any critical alert without `page=production` or with
a pending duration over three minutes,
verifies the exact active group/rule set, removes stale owned groups, and
waits through a bounded evaluation window until every owned rule has completed
a post-sync evaluation and reports `health=ok`; a persistent evaluation error
restores the whole prior namespace and fails the deployment. Backup alerts
retain each metric's source `service`
label (for example, `typesense` or `web-postgresql`) and use
`component=data-backup` for grouping, so simultaneous failures on one host do
not collapse to the same alert label set. This corrects the exporter
alert by selecting only `instance="exporter"` and adds explicit all-host,
disk/inode, sampler, backup, PostgreSQL, Typesense/tunnel, and reboot alerts.
It also routes failed, stale, unresolved, and stuck cross-store reconciliation
state from PostgreSQL-host metrics; reconciliation state does not depend on an
ephemeral crawler process exposing Prometheus.
Production email paging is disabled. The daily Hetzner Codex error-review
issue workflow remains the deduplicated context route; its own failure and
freshness are exported by the crawler-host sampler and remain visible in Mimir.

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

Healthy production has one current `up{job="integrations/unix"}` series for
each stable instance, four `jobseek_alloy_ready == 1` series (three native and
one crawler Compose collector), fresh
`jobseek_host_observability_last_collect_unixtime`, all required probes equal
to one, current backup success timestamps on the two data hosts, PostgreSQL
ready with no new archive failure, and Typesense plus `cloudflared` healthy.
The deployment workflow verifies those custom series after every host rollout;
local timer success and Unix-exporter `up` alone are insufficient evidence.
Treat missing host/sampler series, disk or inode exhaustion, a failed/stale
backup, PostgreSQL archive/readiness failure, or Typesense/tunnel failure as an
incident. Inspect evidence first; this telemetry path does not authorize an
automatic workload restart.

### Production paging is disabled

Grafana Cloud Mimir continues to evaluate the repository-owned service rules,
but Jobseek does not install an email contact, notification route, bridge,
deadman, or synthetic paging test. The former scheduled/manual **Test
Production Paging** workflow is removed and manually disabled in GitHub.

[`scripts/sync-grafana-alertmanager.py`](../scripts/sync-grafana-alertmanager.py)
is now a removal-only utility. It first strips only routes owned by the
`jobseek-production-email` receiver while preserving unrelated policy, then
deletes the Jobseek bridge, deadman, cancelled-test rule, and contact point.
Every observability deployment runs it with `--disable` before syncing Mimir
rules, so a stale production resource is removed rather than reactivated. The
utility has no supported activation mode. Read-only Grafana API checks retry
bounded transient cold-start and rate-limit responses for about two minutes;
writes remain single-attempt so an ambiguous mutation is never replayed.

Do not add a paging schedule, dispatch workflow, contact, route, or activation
command without an explicit new operator decision. Continue using Grafana for
rule state and the deduplicated daily GitHub error-review routine for delivery.

The daily error-review service records an atomic status document before and
after every attempt. The host sampler exports last attempt, last success,
success state, and in-progress state without exposing result text. Failure,
36-hour success staleness, and a run exceeding three hours are independent
critical alerts. If these fire, inspect:

```bash
systemctl status jobseek-codex-daily-error-review.service --no-pager
journalctl -u jobseek-codex-daily-error-review.service -n 160 --no-pager
stat -c '%U:%G:%a %y' /srv/jobseek-codex/state/error-review-status.json
jq '{last_attempt_unixtime,last_success_unixtime,last_attempt_success,run_in_progress}' \
  /srv/jobseek-codex/state/error-review-status.json
```

After repairing the Codex routine, run the service once and require a fresh
successful status so its Mimir alert state resolves normally.

### Telemetry delivery budgets

Grafana Cloud enforces 15,000 active series and 1,500 ingested samples per
second for this tenant. Deployment stops before rule sync unless the total is
at most 12,000 series, crawler application metrics are at most 2,000, Redis is
at most 200, and Unix/textfile host metrics are at most 2,000. The 20% tenant
headroom is an incident buffer, not capacity available to a new unbounded
label. Before adding labels, query the proposed family by label count in
Grafana and state its worst-case fleet cardinality in the change.

Both native and Compose Alloy remote writes are fixed at one shard with a
4,000-sample queue, 500 samples per send, five-second batch deadline, bounded
backoff, and HTTP 429 retry. The crawler path drops
`crawler_host_circuit_*` because `egress_host` grows with every career-site
origin; the `crawler_tasks_total{status=~"host_circuit_.*"}` outcomes preserve
alerting and structured Loki events preserve origin attribution. Redis keeps
capacity, persistence, connection, error, traffic, CPU, and keyspace signals,
but drops per-command histograms.

Compose Alloy has a 512 MiB cgroup limit, a 256 MiB Go soft memory target, and
0.5 CPU. Native Alloy has a 512 MiB systemd hard limit, 448 MiB high watermark,
and 384 MiB Go soft target. The difference between the Go target and hard limit
is required for remote-write WAL mappings, file-backed RSS, runtime overhead,
and log tailing; `docker stats` subtracts inactive file pages and is not the
acceptance measurement. Use the sampler's resident-memory series and the
cgroup/systemd values together.

Production acceptance requires all four collectors ready, their highest sent
timestamp less than three minutes old, zero HTTP 429 responses in the rolling
ten-minute window, no queue above 3,000, and no new failed/dropped samples,
Loki drops, Compose restarts, or OOM flags. Check locally without exposing
credentials:

```bash
curl --fail --silent http://127.0.0.1:12347/-/ready
curl --fail --silent http://127.0.0.1:12347/metrics \
  | grep -E 'alloy_resources_process_resident_memory_bytes|prometheus_remote_storage_(queue_highest_sent_timestamp_seconds|samples_pending|samples_(failed|dropped)_total)'
systemctl show jobseek-alloy.service \
  -p ActiveState -p NRestarts -p MemoryCurrent -p MemoryPeak -p MemoryMax
```

On the crawler, repeat the loopback probes on port `12346` and inspect
`docker inspect deploy-alloy-1` for the 512 MiB limit, restart count, and sticky
OOM flag. Any 429, series-budget breach, stale send timestamp, or new drop is a
telemetry incident even when application services remain healthy.

## Cross-store Reconciliation Timer

`jobseek-crawler-reconciliation.timer` is a Hetzner crawler-host systemd timer,
not a GitHub cron and not an in-process exporter loop. Its first start is 20
minutes after the timer is activated; it launches
`/usr/local/sbin/jobseek-crawler-reconciliation` as the unprivileged `deploy`
user. The wrapper resolves the immutable image digest already deployed in
`/home/deploy/.env` and starts a read-only one-shot container with a 1 GiB
memory limit, one CPU, a PID cap, and no persistent container filesystem. It
explicitly targets Typesense, processes at most 16 partitions, and then exits. Lock acquisition
may wait up to two hours for an authorized deploy/backfill; once acquired, the
container has a separate 50-minute hard runtime cap. The next timer interval
starts only after this service is inactive, preventing delayed work from
causing an immediate second run. The wrapper filters the crawler environment
into a mode-`0600` ephemeral file containing only `LOCAL_DATABASE_URL` and four
Typesense settings. Neither the retired crawler mirror credential nor
`WEB_DATABASE_URL` enters the one-shot container; proxy, R2, Redis, Codex,
Murmur, and other unrelated credentials are also excluded, and the file is
removed on every exit path. It invokes the installed `/app/.venv/bin/crawler` entry point
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
root, then queues one immediate bounded reconciliation run. A failed install
restores the previous files. The deployed commit is written atomically to
`/var/lib/jobseek-reconciliation/deployed-sha`: the directory is
`root:deploy 0750` and the file is `root:deploy 0640`, so the unprivileged
service can read the revision without being able to replace it. The installer
also atomically publishes the exact installed wrapper digest to the
group-readable `wrapper-sha256` state file as its final compatibility marker.
Reinstalling the host surface repairs missing or corrupt state and normalizes
incorrect ownership. Before changing credentials, files, or services, the
crawler deploy waits for the installed wrapper content and completed-install
marker to match the digest from its own revision; a timeout fails closed. The
reconciliation deploy, crawler deploy, and crawler-host observability deploy
verify the installed state and active timer appropriate to their boundary
before succeeding. The timer remains enabled for subsequent hourly slices;
the application deploy owns Alembic and the additive Typesense schema patch.

The earlier `jobseek-reconciliation-typesense-catchup.service` and matching
timer are retired. They are not a failover path: the canonical timer above is
the only authorized scheduler. The fleet hygiene baseline keeps both obsolete
names persistently masked and reset while requiring the canonical timer to be
enabled and active before and after retirement. An archived copy of an exact
old `/etc/systemd/system` unit may remain under
`/var/lib/jobseek-host-hygiene/retired-units/<revision>/` for forensic review;
it is not executable from that location.

Some systemd releases report an exact `/etc/systemd/system/<unit> -> /dev/null`
mask as `LoadState=not-found` after reload. Host hygiene accepts that portable
representation only when the allowlisted path is a symlink whose canonical
target is `/dev/null`, `UnitFileState` and `systemctl is-enabled` both report
`masked`, the unit is inactive, and `systemctl is-failed` does not report a
failure. An absent path, regular file, or symlink to any other target remains
nonconformant.

The 2026-07-23 outage was an ownership regression, not a cleanup operation.
The revision preflight was added while the state directory was still created
as `0700 root:root`. The root deployment check passed, but every service run as
`deploy` failed before starting a container. Non-root inspection reported the
protected file as unavailable, which looked like deletion. The first failure
therefore coincided exactly with the revision preflight rollout. No crawler,
observability, backup, ingress, or Docker lifecycle path removes this state
directory.

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
`jobseek_cross_store_reconciliation_*` series. The PostgreSQL-host sampler
filters the rollback-compatible state table to `target='typesense'`, so the
obsolete Supabase state row cannot create stale production alerts. Healthy
production has a full verified Typesense cycle within 30 hours, zero unresolved
drift, no run older than two hours still marked running, and bootstrap
complete. The crawler-host collector also publishes the current
revision as `jobseek_cross_store_reconciliation_deployed_revision_info`, its
file mtime, and a boolean availability series. A missing/inaccessible revision
alerts after three minutes; no completed hourly slice within 2.5 hours alerts
independently of the 30-hour full-cycle freshness budget.

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
  --full
```

Inspect the Typesense aggregate result before continuing.
If the cap is reached, the verified partition cursor remains resumable; rerun
the same command rather than increasing limits during an incident. Confirm the
interrupted run is recorded and that the next invocation resumes at the last
verified cursor. Stopping or disabling the timer is a scheduling rollback
only—the migration and optional Typesense bucket field are additive, and
disabling the timer does not undo already verified downstream repairs.

## ATS Inventory Candidate Timer

`jobseek-ats-inventory.timer` runs the data-only company inventory and impact
refresh daily on the crawler host, with persistent catch-up and a 45-minute
random delay. It uses the immutable crawler image named by the atomic committed
release marker (published after crawler health and rollback disarm) and never runs
Codex or upstream scraper code. Three root-owned GitHub App credentials enter
the service through systemd `LoadCredential`; only a short-lived installation
token file is mounted into the read-only one-shot container. No GitHub token,
private key, crawler environment file, database credential, or issue body is
written to Docker metadata or the operator status.

The first install is report-only and has an independent `writes-disabled`
sentinel. The timer can remain active while writes are disabled: source/cache
validation and queue reporting continue, while candidate/support issue POSTs
cannot occur. Cache and ledger data under `/var/lib/jobseek-ats-inventory`
survive disable, failed installs, and transactional host-surface rollback.
Disabling also stops an active run after publishing the gate; the wrapper
rechecks that gate immediately before its credentialed GitHub phase.

Host-surface deployment verifies the committed crawler tag, manifest digest, and full Git
revision while holding `/run/lock/jobseek-crawler-mutation.lock`, then pins that
exact release for a report-only acceptance run. The prior runner remains the
rollback target until the fresh report succeeds and the timer is active. A stop
timeout, failed report, stale status, or mismatched release fails the install and
restores the prior units, credentials, and scheduling state. Acceptance uses a
disposable cache; the independent operator write gate is never part of the
rollback snapshot, so a concurrent emergency disable cannot be undone. Root
creates a missing post-reboot mutation-lock inode through a deploy-user process
as `deploy:deploy 0600` and opens it read-only, avoiding a root-owned creation
window and preserving the shared cross-user lock contract. The runner container
uses the dedicated IPv4-only `jobseek-ats-inventory-egress` bridge. Before every
run, `jobseek-ats-inventory-network.service` rebuilds a fail-closed
`DOCKER-USER` policy that rejects the host and private/reserved destinations,
allows only public HTTPS/DNS, and disables inter-container communication. A
credential-free container probe must reach GitHub and the inventory source while
failing TCP connects to both the crawler host and the exact production
PostgreSQL address. This closes both the loopback Redis and routed private-DB
paths; a missing rule, stale network, DNS failure, or reachable blocked endpoint
prevents the runner from starting.

Read-only checks:

```bash
systemctl is-enabled jobseek-ats-inventory.timer
systemctl is-active jobseek-ats-inventory.timer
systemctl status jobseek-ats-inventory-network.service --no-pager
systemctl list-timers --all jobseek-ats-inventory.timer --no-pager
/usr/local/sbin/jobseek-ats-inventory-control status
python3 -m json.tool /var/lib/jobseek-ats-inventory/status/current.json
journalctl -u jobseek-ats-inventory.service --since '24 hours ago' --no-pager
```

The host sampler exports `jobseek_ats_inventory_*` aggregate series for
freshness, success, coverage, queue health, imported issue lifecycle, pickup
latency, creates, and rollout state. A missing status is explicitly exported
as unavailable. The complete deployment, report/dry-run/refill controls,
stage-1/5/25 evidence gates, and emergency disable procedure are documented in
[`21-ats-inventory-runner.md`](21-ats-inventory-runner.md#hetzner-deployment-and-rollout).

## Board Quarantine Recovery

Ordinary monitor failures are recoverable. After five consecutive failures a
configured board remains enabled with `board_status='quarantined'`; its
`next_check_at` continues the exponential schedule capped at 24 hours. Redis
puts those probes in the recurring tier, so provider/domain throttles bound
pressure and a deploy cannot create a first-time retry storm.

The PostgreSQL host sampler exports the durable cohort:

- `jobseek_crawler_quarantined_boards`
- `jobseek_crawler_quarantine_oldest_seconds`
- `jobseek_crawler_quarantine_active_postings`
- `jobseek_crawler_board_recoveries_total`

Inspect the source of truth without changing it:

```bash
docker exec -i postgres psql -U crawler -d crawler -X -v ON_ERROR_STOP=1 -c "
SELECT board_slug, crawler_type, consecutive_failures,
       quarantine_probe_count, quarantined_at, next_check_at,
       left(last_quarantine_error, 160) AS last_quarantine_error
FROM job_board
WHERE board_status = 'quarantined'
ORDER BY next_check_at, board_slug;"
```

Recovery rules:

1. Do not set a quarantined row to `active` manually and do not delist its
   postings. A successful provider-native monitor run is the proof that moves
   the row to `active`, records `last_recovered_at`, and increments
   `recovery_count`.
2. Fix monitor code or the CSV-owned monitor configuration normally. `crawler
   sync` fingerprints the monitor contract; a real config change resets the
   retry ramp, makes the probe due immediately, and keeps the row quarantined
   until that probe succeeds.
3. A failed recovery probe stays quarantined and receives another bounded
   backoff. Ordinary 401/403/429/5xx, timeout, transport, and parser failures
   never become terminal retirement evidence.
4. Only an explicit operator retirement or the separately reviewed, spaced
   provider-gone confirmation policy may stop scheduling a configured board.
5. Run the phantom-posting sweep only after live boards recover and the
   remaining sources have verified-dead evidence. This prevents stale active
   rows from being tombstoned before their owner can publish a current diff.

Migration 0015 prioritizes the deterministic Ashby recovery cohort
immediately and spreads other legacy disabled boards across six hours. The
deploy-time sync then re-disables historical rows absent from `boards.csv`, so
only configured sources enter Redis.

## Phantom Active Posting Sweep

The fail-closed `sweep-phantoms` command repairs active postings only after
their owning board has a terminal classification. It never touches
`quarantined` or `gone_pending` boards. A `disabled` board is eligible only
when its exact URL is absent from the deployed `boards.csv`; a configured
disabled board with active postings aborts the entire mutation and must first
recover through the provider-native monitor path. A configured `gone` board
is eligible only after the spaced provider-gone policy has recorded its
terminal timestamp and at least two confirmations.

Start with the read-only classification from a deployed crawler container:

```bash
docker exec deploy-worker-1-1 uv run --no-sync crawler sweep-phantoms --dry-run
```

The live invocation holds a PostgreSQL session advisory lock, commits at most
1,000 rows per transaction, and rechecks terminal board state inside every
chunk. `FOR UPDATE SKIP LOCKED` lets live workers finish rows they already
own. The default invocation is capped at 100 chunks; if `remaining_postings`
is nonzero, rerun the same command rather than increasing limits during an
incident:

```bash
docker exec deploy-worker-1-1 uv run --no-sync crawler sweep-phantoms
```

Every tombstone sets `updated_at=clock_timestamp()` so the ordered exporter
publishes it through the normal local PostgreSQL-to-Typesense CDC path. The
command also invalidates `cache:platform-stats` and recomputes Typesense
company/taxonomy counts. A signal or failure rolls back only the current
chunk; already committed chunks remain safe and the next invocation resumes
from the remaining active rows.

The PostgreSQL host sampler checks the invariant every minute:

- `jobseek_crawler_phantom_active_boards`
- `jobseek_crawler_phantom_active_postings`
- `jobseek_crawler_phantom_active_oldest_seconds`

`CrawlerPhantomActivePostings` fires after 15 minutes of nonzero drift. After
a repair, require the local count to be zero, wait for the exporter cursor to
pass the repair timestamps, and compare exact active posting IDs in local
PostgreSQL and Typesense for every affected company. Do not clear the alert or
edit exporter cursors manually.

## Provider-Gone Confirmation and Recovery

An explicit provider-native retirement signal such as a board API 404 is not
terminal on its own. It moves the configured board to `gone_pending`, retains
its active postings, and schedules the next confirmation six hours later.
Boards successful during the preceding seven days need three spaced
confirmations; older sources need two. Only the terminal transition delists
the board's postings.

Confirmed-gone configured boards remain enabled and receive a provider-native
probe every 24 hours. A valid non-empty or empty response moves the row back to
`active` (or the normal empty-board `suspect` state), increments the durable
recovery counter, and lets the standard posting diff relist matching jobs.
Removing the board from `boards.csv` remains the only configuration-owned
terminal disable.

The PostgreSQL host sampler exports:

- `jobseek_crawler_gone_pending_boards`
- `jobseek_crawler_gone_pending_confirmations`
- `jobseek_crawler_gone_pending_oldest_seconds`
- `jobseek_crawler_gone_terminal_boards`
- `jobseek_crawler_board_gone_transitions_total`
- `jobseek_crawler_board_gone_recoveries_total`

Inspect without changing state:

```bash
docker exec -i postgres psql -U crawler -d crawler -X -v ON_ERROR_STOP=1 -c "
SELECT board_slug, crawler_type, board_status, is_enabled,
       gone_confirmation_count, gone_first_confirmed_at,
       gone_last_confirmed_at, next_check_at, last_gone_status,
       left(last_gone_endpoint, 160) AS last_gone_endpoint,
       left(last_gone_error, 160) AS last_gone_error
FROM job_board
WHERE board_status IN ('gone_pending', 'gone')
ORDER BY next_check_at, board_slug;"
```

Do not advance confirmation timestamps or set a row active manually. For a
live source, run the supported `crawler board <board-slug>` path or wait for
the durable Redis schedule; the successful provider response is the recovery
proof. For a stale pending alert, compare `next_check_at` with the Redis task,
check the stored endpoint/status, and inspect provider-wide failures before
accepting a terminal transition. Migration 0016 treats every legacy one-shot
`gone` row as one unconfirmed observation, schedules it within fifteen minutes,
and lets the deploy-time CSV sync disable rows that are no longer configured.

## Fail-Closed Stale-Board Retirement Report

`retire-stale-boards` is a read-only evidence report. Database state selects
the candidates, but does not authorize removal. The command loads the exact
deployed `companies.csv` and `boards.csv`, probes supported provider-native
listing endpoints with bounded concurrency, and separates the result into:

- `verified_gone`: a current provider-gone result plus at least two durable
  confirmations spaced by six hours;
- `live_again`: a current valid response, including a valid board with zero
  jobs; route these boards through the normal provider-native recovery run;
- `probe_inconclusive`: unsupported probes, 429s, timeouts, transient 5xx
  responses, redirects that need review, or a gone result still waiting for
  durable confirmation;
- `integration_broken`: registry/runtime drift, invalid configuration, or an
  unexpected provider response contract;
- `zero_board_registry_orphans`: company rows with no configured board rows.

Run it from a deployed crawler container so the report uses the same registry
and network path as production:

```bash
docker exec deploy-worker-1-1 uv run --no-sync crawler retire-stale-boards \
  --days 14 --format md --probe-concurrency 5
docker exec deploy-worker-1-1 uv run --no-sync crawler retire-stale-boards \
  --days 14 --format json --probe-concurrency 5
```

Every evidence row includes a UTC probe timestamp, stable reason code,
endpoint class and URL, HTTP status, redirect target, job count when the
provider exposes a reliable total, and current company board context. The
JSON form is the automation contract.

The `shell` format is deliberately fail closed. It emits executable CSV
removal commands only for `verified_gone` board candidates and companies for
which every configured board independently passed the same current and
durable confirmation gates. Live, rate-limited, transient, unsupported,
unconfigured, and otherwise inconclusive candidates appear only as comments:

```bash
docker exec deploy-worker-1-1 uv run --no-sync crawler retire-stale-boards \
  --days 14 --format shell --probe-concurrency 5
```

Do not convert a non-executable section into a manual removal command. Recover
`live_again` boards with `crawler board <board-slug>` and let the normal success
transition reset terminal state. Retry transient probes after backoff. Repair
integration failures before repeating the report. Zero-board registry orphans
require separate operator review; the report never assumes they are dead.

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
- retain the stopped `jobseek-web-postgresql-backup-image-lease` container on
  the Typesense host; it references the exact digest-pinned PostgreSQL helper
  image so both normal and emergency image pruning treat that backup dependency
  as in use
- on the crawler host, keep running images plus the two newest versioned
  `jobseek-crawler` and `jobseek-crawler-browser` images, then remove older
  unused version tags immediately

The crawler-specific rule matters because repeated versioned deploys can
consume tens of GiB before a normal age-based prune would trigger.
Typesense snapshot staging is intentionally outside `/`; a backup must not be
used to justify or trigger the below-5-GiB all-unused-image emergency path.

Before emergency all-image pruning on the Typesense host, verify the web
PostgreSQL helper lease described in
[`19-data-backup-recovery.md#web-postgresql-backup-operation`](19-data-backup-recovery.md#web-postgresql-backup-operation).
Do not add `docker container prune` to this policy: stopped containers may own
an intentional image-lifecycle contract. The regression boundary is the exact
sequence installer digest pull/lease creation, below-floor
`docker image prune --all --force`, then the scheduled backup dependency check.
The repository's real-Docker regression performs that destructive prune only
on an explicitly acknowledged GitHub-hosted ephemeral runner; never enable it
on a managed or developer host.

## Fleet Host and Log Hygiene

[`jobseek-host-hygiene.py`](../scripts/jobseek-host-hygiene.py) is the
fail-closed conformance boundary for the crawler, PostgreSQL, and Typesense
hosts. It reports:

- any failed service or timer;
- any exited container without a Compose, maintenance, backup, or explicit
  Jobseek ownership label;
- missing or unbounded `json-file` logging on the standalone `postgres` and
  `typesense` containers;
- a missing or altered role-specific journald policy; and
- on the crawler, an unhealthy canonical reconciliation timer, either retired
  catch-up name not masked/inactive/reset, or an unexpected second
  reconciliation timer.

`Deploy Hetzner Host Hygiene` runs that audit read-only across all three hosts
each day. Scheduled execution cannot install policy or invoke cleanup; both
mutating jobs additionally require an explicit workflow dispatch and the
production environment approval gate. Every connection is fail-closed against
the reviewed host identity through native OpenSSH and strict host-key checking.
The crawler uses `HETZNER_CRAWLER_KNOWN_HOSTS`, PostgreSQL uses the existing
`HETZNER_BACKUP_KNOWN_HOSTS` key set already scoped to its production backup
transport, and Typesense uses `HETZNER_TYPESENSE_KNOWN_HOSTS`. The transport
requires an exact `TARGET_HOST` entry before connecting and never substitutes
one role's trust material for another. Trust is never learned from the network
with `accept-new` or `ssh-keyscan`, and third-party SSH/SCP actions are not in
this workflow.

Apply payloads are copied only after the workflow creates and verifies
`/var/lib/jobseek-host-hygiene/staging/<revision>-<run>-<attempt>` beneath a
root-owned, mode-`0700`, non-symlink trust boundary. The install connection
rechecks every boundary component, rejects any symlink or non-root-owned entry,
removes group/other permissions, and executes only the verified regular files
inside that boundary. No root-executed payload is staged under `/tmp`.

The canonical database and search container contract is `json-file` with
`max-size=50m` and `max-file=3`. Typesense already enforces this in its host
installer. Both PostgreSQL creation paths enforce and verify the same values;
the ingress conformance probe treats an otherwise healthy but unbounded
database container as nonconformant so the existing staged PostgreSQL
replacement can repair it behind its backup/readiness/private-path rollback
gates.

Journald retention is explicit per host:

| Role | Persistent maximum | Keep free | Maximum file | Retention | Runtime maximum |
|---|---:|---:|---:|---:|---:|
| crawler | 2 GiB | 5 GiB | 128 MiB | 7 days | 256 MiB |
| PostgreSQL | 2 GiB | 5 GiB | 128 MiB | 7 days | 256 MiB |
| Typesense | 1 GiB | 5 GiB | 128 MiB | 7 days | 256 MiB |

The seven-day time ceiling exceeds the 25-hour remote-log window and retains
multiple daily backup/reconciliation cycles. Size and free-space ceilings can
expire older history first during pressure. The installer creates persistent
journal storage, installs
`/etc/systemd/journald.conf.d/60-jobseek-retention.conf`, restarts journald
only when that policy changes, verifies the journal, and keeps a root-only
rollback snapshot. It never runs `journalctl --vacuum-*` or Docker prune.

### Reviewed rollout

1. Require the critical backup repairs to be healthy and record a fresh
   PostgreSQL backup plus repository check. Run the Hetzner ingress workflow in
   `audit` mode. After merge, run it in guarded `apply` mode once; an
   unbounded PostgreSQL log contract now forces the transactional database
   replacement instead of being mistaken for an already-compliant host.
   Require private-path validation, `pg_isready`, pgBackRest check, and commit
   of the rollback container before continuing.
2. Run `Deploy Hetzner Host Hygiene` with `action=audit`. Review every failed
   unit and exited-container finding. Then run `action=apply`; this installs
   each journal budget and, on the crawler only, archives/masks/resets the
   exact retired catch-up service and timer. Apply reports remaining cleanup
   but does not remove a container.
3. On PostgreSQL, locate the audited unmanaged exited container read-only and
   capture all immutable fields in one review record:

   ```bash
   docker inspect --format \
     '{{.Id}} {{.Image}} {{.Config.Image}} {{.Created}} {{.State.FinishedAt}} {{.State.ExitCode}} {{.Name}}' \
     <candidate-container>
   docker image inspect --format '{{.Id}} {{json .RepoDigests}}' <full-image-id>
   ```

   The human-readable/random Docker name is discovery evidence only. It never
   authorizes removal. Do not print the container environment, command, mount
   sources, or raw inspect JSON into CI logs; they can contain credentials or
   private paths. The cleanup verifier checks ownership labels locally without
   echoing their values.
4. Enter the full container ID, full `sha256:` image ID, exact creation and
   finish timestamps, and exact exit code into the production-approved
   `cleanup` action. The command performs an identity-bound dry run first,
   re-inspects immediately, refuses running/dead/restarting or Jobseek-managed
   namespaces, and then invokes only `docker rm -- <full-id>` (no force, volume
   removal, wildcard, or prune). Any changed field aborts without mutation.
5. Run the workflow `audit` action again. Record the redacted JSON output and
   these independent checks:

   ```bash
   systemctl is-enabled jobseek-crawler-reconciliation.timer
   systemctl is-active jobseek-crawler-reconciliation.timer
   systemctl list-unit-files --type=timer | grep reconciliation
   systemctl --failed --no-pager
   docker inspect postgres --format '{{json .HostConfig.LogConfig}}'
   docker inspect typesense --format '{{json .HostConfig.LogConfig}}'
   journalctl --disk-usage
   systemd-analyze cat-config systemd/journald.conf
   ```

Acceptance is one enabled/active canonical reconciliation scheduler, both
retired names masked/inactive and absent from failed units, no unexpected
failed units or unmanaged exited containers, exact bounded log settings on
both standalone services, and the role-specific journal drop-in active. Keep
at least one normal reconciliation cycle, one PostgreSQL backup cycle, and one
Typesense backup cycle in the post-rollout observation window before closing
the issue.

### Rollback

The PostgreSQL logging change uses the ingress workflow's existing staged
rollback container and 15-minute automatic rollback timer; do not remove that
container until private-path and database checks pass. A failed journal-policy
install restores its exact prior policy/verifier and restarts journald. After a
successful install, an operator can restore the named snapshot under
`/var/lib/jobseek-host-hygiene/rollback/` and restart journald if retention
causes a measured regression.

Do not automatically unmask the retired catch-up units during a journal or
database rollback. If investigation proves one was misclassified, first stop
the canonical timer, demonstrate why the old unit does not create duplicate
scheduling, restore only its exact archived file, and obtain a separate
review. Normal rollback leaves the obsolete names masked.

The older crawler-only
[`crawler-host-hygiene.py`](../scripts/crawler-host-hygiene.py) check remains a
read-only, 24-hour detector for running unmanaged containers and active-exited
transient services in scheduled crawler maintenance. It does not replace the
fleet conformance or authorize cleanup.

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

Do not add another scheduler for these routines. Manual recovery invokes the
same committed runner entry point once from a throwaway worktree, with the
Hetzner ledger, shared lock, and `ws` claims checked first. Keep
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

### Production Maintenance Provenance

Do not run ad-hoc crawler one-offs or pause writer services with bare
`docker run`, `docker compose run`, `docker compose stop`, or `docker stop`.
Use the installed `/usr/local/sbin/jobseek-maintenance` contract. It:

- requires a lowercase operation slug, positive GitHub tracking issue, full
  reviewed Git revision, and a 60-second to 8-hour runtime budget;
- holds `/run/lock/jobseek-crawler-mutation.lock` for the whole operation;
- owns the exact Compose and `jobseek.maintenance.*` labels consumed by the
  lifecycle watcher and rejects attempts to override them;
- never logs the wrapped command or environment;
- terminates an over-budget process group, cleans a named one-off, and fails
  when the expected crawler services are not running/healthy afterward; and
- creates a constrained, networkless marker before a multi-command pause
  window, so service stops and restoration are correlated to the operation
  even when the inner repair has several containers.

The lock inode is shared by root-run operator work and deploy-user workflows.
The wrapper opens an existing lock read-only before applying `flock`; it never
creates/truncates that inode merely to acquire it. If root must create a
missing post-reboot lock, ownership is handed to `deploy` so later workflows
continue to serialize on the same inode.

The revision must be the reviewed commit that defines the repair. Keep
credentials in a root/deploy-only environment file; never put them in the
wrapper or Docker command arguments.

For one bounded Docker one-off:

```bash
/usr/local/sbin/jobseek-maintenance oneoff \
  --operation repair-example \
  --issue 1234 \
  --revision <40-character-reviewed-git-sha> \
  --budget-seconds 1800 \
  -- \
  docker run --rm --name repair-example \
    --env-file /run/lock/repair-example.env \
    --network host \
    "$(sed -n 's/^CRAWLER_IMAGE_REF=//p' /home/deploy/.crawler-active-release/success.env)" \
    /app/.venv/bin/crawler <bounded-command>
```

For a complex operation that pauses and restores Compose services, put the
reviewed steps in a root/deploy-owned script and wrap the entire script:

```bash
/usr/local/sbin/jobseek-maintenance window \
  --operation repair-example \
  --issue 1234 \
  --revision <40-character-reviewed-git-sha> \
  --budget-seconds 1800 \
  -- /home/deploy/maintenance/repair-example.sh
```

The inner script is still responsible for its domain preconditions, backups,
and ordered stop/start logic. The wrapper provides serialization, attribution,
budget enforcement, cleanup, and post-operation health gates. A failed or
interrupted wrapper must not be treated as successful merely because the
services later happen to be running.

Repository-owned automated operations use the same contract:

Full crawler deploys remain in the `crawler-production-sync` GitHub Actions
group with protected location repair and destructive web migration. CSV-only
publication uses the separate `crawler-production-csv-sync` group so a pushed
or explicitly dispatched sync cannot replace a pending full deploy; GitHub's
default concurrency queue keeps only one pending run per group. Both crawler
deploy and CSV sync acquire `/run/lock/jobseek-crawler-mutation.lock`, so the
separate GitHub queues remain serialized at the Hetzner mutation boundary.
The CSV sync resolves its runtime credentials and the currently committed
crawler image only after the maintenance wrapper owns that lock. This prevents
a queued sync from retaining an environment or image snapshot taken while a
full deployment was still replacing `/home/deploy/.env`.

- crawler deploys bracket their writer pause, schema migration, Typesense
  setup, and CSV sync under `crawler-deploy`, tracking issue #3409;
- pushed CSV changes use the bounded wrapper as `csv-data-sync`, tracking
  issue #2623;
- weekly currency refresh uses the bounded wrapper as
  `refresh-currency-rates`, tracking issue #3576;
- scheduled Typesense maintenance attaches exact labels for tracking issue
  #2630; and
- cross-store reconciliation attaches exact labels for tracking issue #5930.

The direct-label deploy/reconciliation paths validate a full Git revision,
share the crawler mutation lock, enforce a finite execution/window budget,
and clean up their marker or one-off. Missing, partial, invalid, or
conflicting labels remain unattributed by design.

The root error-review collector writes:

- `host/docker-lifecycle.jsonl`: allowlisted events with pseudonymous
  container generations (legacy raw IDs are one-way transformed before the
  runner can read them); and
- `host/maintenance-correlation.json`: command-free attributed windows,
  service downtime, forced termination/OOM evidence, one-off exits, budget
  result, and restoration state.

Verify the installed contract and privilege boundary:

```bash
python3 /usr/local/sbin/jobseek-maintenance --self-test
stat -c '%U:%G:%a' /usr/local/sbin/jobseek-maintenance
id -nG codex-runner | tr ' ' '\n' | grep -qx docker && echo 'unexpected docker group'
sudo -u codex-runner test ! -w /var/run/docker.sock
sudo -u codex-runner docker ps >/dev/null 2>&1 && echo 'unexpected Docker access'
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
bash -lc 'source /srv/jobseek-codex/repo/scripts/deploy-codex-runner-host.sh; _validate_labeller_env_file /etc/jobseek-codex/labeller.env'
sudo -u codex-runner test ! -w /var/run/docker.sock
```

Smoke-test the read-only annotation database role without printing the DSN:

```bash
sudo -iu codex-runner bash -lc 'set -a; . /etc/jobseek-codex/labeller.env; set +a; cd /srv/jobseek-codex/repo/apps/crawler && .venv/bin/python - <<'"'"'PY'"'"'
import asyncio
import os
import asyncpg

async def main():
    conn = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
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
test -s /srv/jobseek-codex/inputs/error-review/latest/host/maintenance-correlation.json
sudo -u codex-runner test -r /srv/jobseek-codex/inputs/error-review/latest/manifest.json
sudo -u codex-runner test -r /srv/jobseek-codex/inputs/error-review/latest/host/maintenance-correlation.json
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
grep -E '^(CRAWLER_IMAGE_TAG|CRAWLER_IMAGE_REF|BROWSER_IMAGE_REF|SHIM_IMAGE_REF)=' \
  /home/deploy/.env
cat /home/deploy/.crawler-active-release/success.env
docker images 'ghcr.io/colophon-group/jobseek-crawler*' \
  --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}} {{.CreatedSince}}'
```

Treat the digest references in the atomic success marker and rollback `.env`
as authoritative. Keep their crawler/browser images and at least one recent
rollback pair. Version tags are only human-readable aliases; remove older
unused aliases/images with `docker rmi <image-ref>`.
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

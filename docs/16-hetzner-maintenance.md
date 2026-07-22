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
  directly to Grafana Cloud. No host opens a scrape port.
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
readiness/connections/WAL archive/checkpoint/database size, and Typesense
health/tunnel state. Sticky Docker OOM flags and absolute restart counters are
evidence only; the daily error review applies generation/time-window rules
before declaring a new incident.

Deployment is owned by
[`deploy-hetzner-observability.yml`](../.github/workflows/deploy-hetzner-observability.yml).
It validates the Python, shell, Alloy, alert, and systemd contracts; deploys
the crawler, PostgreSQL, and Typesense hosts sequentially; then syncs the
single Mimir rule group only after every host is healthy. Environment-scoped
host variables are resolved inside runtime steps after the protected
`production` environment is attached. The installer snapshots the prior
binary, configuration, secret env, and units under the root-only
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
this tenant to 20 rules per group, so the source separates fleet and crawler
alerts into two logical groups below that limit. The sync client first
captures the complete owned namespace, requires every alert to have a
repository runbook plus `owner=codex-error-review` and `route=codex-daily`,
verifies the exact active group/rule set, removes stale owned groups, and
restores the whole prior namespace on failure. This corrects the exporter
alert by selecting only `instance="exporter"` and adds explicit all-host,
disk/inode, sampler, backup, PostgreSQL, Typesense/tunnel, and reboot alerts.
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
Treat missing host/sampler series, disk or inode exhaustion, a failed/stale
backup, PostgreSQL archive/readiness failure, or Typesense/tunnel failure as an
incident. Inspect evidence first; this telemetry path does not authorize an
automatic workload restart.

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

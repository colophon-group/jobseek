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

SSH pattern:

```bash
ssh -i ~/.ssh/hetzner_deploy root@<HOST>
```

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

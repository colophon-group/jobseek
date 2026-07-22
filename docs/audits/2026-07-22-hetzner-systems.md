# Hetzner systems audit — 2026-07-22

Status: evidence collection complete for the three known non-Murmur hosts. Remediation is intentionally paused pending operator approval.

This report records read-only evidence. It omits public IP addresses, credentials, full connection strings, and authentication material. Murmur is excluded. Hetzner Cloud control-plane settings such as provider snapshots and provider firewalls could not be inspected because no Hetzner API context or token was available; externally observed reachability and host-local controls were still tested.

## Inventory and ownership

| host | role | platform | persistent data | deployment/owner surface |
|---|---|---|---|---|
| `jobseek-crawler-browser` | HTTP workers, browser worker, exporter, R2 drain, Redis, Alloy, Codex runners | Ubuntu 22.04; 8 vCPU; 15 GiB RAM; no host swap; Docker/Compose plus systemd | Redis Docker volume; Alloy position volume; `/srv/jobseek-codex` state, inputs, traces, and worktrees | crawler stack: `.github/workflows/deploy-crawler-browser.yml` and `/home/deploy/deploy.sh`; data sync/maintenance: GitHub workflows; Codex: repo-owned systemd units deployed by `.github/workflows/deploy-codex-runner.yml`; maintainer/operator ownership is implicit rather than named |
| `jobseek-postings-postgresql` | authoritative crawler PostgreSQL | Ubuntu 24.04; 4 vCPU; 7.6 GiB RAM; no swap; `postgres:16-alpine` with host networking | 20 GiB XFS Hetzner volume mounted under `/mnt`; PostgreSQL database is about 16 GiB | manually operated Docker container; no repo-owned host deploy, backup job, or named service owner found |
| `jobseek-typesense` | Typesense and Cloudflare Tunnel | Ubuntu 24.04; 2 vCPU; 3.7 GiB RAM; no swap; `typesense/typesense:27.1` with host networking | `/mnt/typesense-data`, about 1.8 GiB | manually operated Docker container and systemd `cloudflared`; collection schema/data are rebuilt from crawler deploy and maintenance paths; no named host owner found |

## Host coverage matrix

| control | crawler/Codex | PostgreSQL | Typesense | result |
|---|---|---|---|---|
| OS, kernel, packages, reboot | inspected | inspected | inspected | all three have `/var/run/reboot-required`; 26/46/58 packages pending; crawler and Typesense include security updates |
| SSH/access | effective sshd config, account locks, keys, isolation checked | effective sshd config, account status, keys checked | effective sshd config, account locks and keys checked | password authentication is enabled fleet-wide; PostgreSQL permits root login and its root password is set; Internet brute-force noise is substantial |
| ingress/firewall/private network | host listeners, UFW and external probes checked | host listeners, UFW, PostgreSQL bind/HBA and external probes checked | UFW, private port, public tunnel, DNS and external probes checked | crawler and PostgreSQL have no active host firewall; crawler operational endpoints and PostgreSQL are Internet-reachable; Typesense origin port is correctly private-only |
| Docker/service state | containers, limits, restarts, OOM history, image tags/digests checked | container state, memory, restart/OOM, config checked | container state, memory, restart/OOM, config checked | current services healthy; crawler OOM history predates the verified fixes in #5102/#5798/#5800 |
| CPU/RAM/swap/load/disk/inodes | inspected | inspected | inspected | PostgreSQL data volume is 92% full; other filesystems and inodes have headroom; no host has swap |
| backups/restore | Redis is explicitly disposable; Codex SQLite state present; no host recovery test | no local backup/archive/replica evidence; provider snapshots claimed in docs but not verifiable; no restore evidence | no local backups; index is documented as rebuildable from PostgreSQL | authoritative PostgreSQL recovery is unproven and is the primary durability gap |
| secrets/config permissions | deploy env is 0600; Codex runner cannot read env or Docker | Docker metadata is root-only; no deploy source-of-truth found | Cloudflare token and Typesense admin key are in process arguments visible to unprivileged local users | Codex isolation is good; Typesense host secret delivery must change and affected credentials must later be rotated |
| TLS/DNS | no public app endpoint in scope | PostgreSQL TLS disabled; private use intended but public listener exists | Cloudflare DNS/TLS valid; health public; API requires a key; documented rate limit not independently verifiable | tunnel edge is healthy; database exposure violates its intended private-network design |
| monitoring/alerts | Alloy and host exporter present | absent | only indirect Typesense health/memory from crawler exporter | Grafana sees crawler host only; PostgreSQL and Typesense host disks/OS are absent; `DiskNearFull` cannot protect them |
| maintenance/runbooks | deploy, Docker GC, Codex, error review documented | resize guidance only | rebuild guidance documented | no complete fleet patch, backup/restore, incident-owner, or host rebuild runbook |

## Service coverage matrix

| service/path | live evidence | classification |
|---|---|---|
| HTTP workers 1–3 | current `v0.13.163`, healthy; 1 GiB each; 75 cgroup OOM kills in seven days all belong to pre-fix generations, with no recurrence after the production-verified fixes | healthy now; resolved historical incident, no duplicate issue |
| browser worker | current `v0.13.163`, healthy; 6 GiB limit | healthy; prior OOM issue already resolved |
| exporter | current cursor legs equal and current; Supabase and Typesense writes succeed | CDC loop healthy, but downstream data drift exists because reconciliation is effectively starved by deploys |
| R2 drain | running; no current restart/OOM evidence | healthy |
| Redis | `redis:8-alpine`; RDB persistence healthy; 674 MiB used of 1 GiB maxmemory; no evictions/rejections; no AOF; documented disposable/reseedable state | healthy; mutable image tag belongs in lifecycle hardening |
| Alloy/Grafana | remote write and logs healthy; operational ports bind publicly; host exporter covers only crawler | functional but incomplete and unnecessarily exposed |
| Codex governor | timer enabled and admitting checks; scheduler skips because every remaining company request already has an open draft PR | healthy; absence of new company runs is expected |
| daily annotations | failed after two 30-second PostgreSQL statement timeouts; final HuggingFace 404 was downstream symptom | confirmed root cause: active 24-hour sample scans/sorts the active set because no `first_seen_at` index supports the query |
| daily error review | completed; evidence bundles and lifecycle journal present | healthy |
| PostgreSQL | PostgreSQL 16.13; about 2.44M postings and 2.11M descriptions; descriptions relation about 14 GiB; 51,106 requested versus 6,330 timed checkpoints; `max_wal_size=1GB`; 2 GiB container limit | active capacity/performance and durability incident |
| Typesense | 27.1; health OK; seven aliases point to seven versioned collections; total posting documents differ from local PostgreSQL by only about 10 at observation | structurally healthy, but `is_active` field drift exists and secrets are delivered unsafely |
| Cloudflare Tunnel | active; public DNS resolves through Cloudflare; certificate valid; unauthenticated collections rejected | healthy edge; token delivery unsafe |
| refresh-typesense | scheduled workflow generally succeeds, with two recent failures among ten sampled runs | operating; reconciliation semantics are insufficient for field drift |
| IndexNow | scheduler intentionally retired in #2821; code/table remain | intentionally inactive, not an incident |
| local PostgreSQL → Supabase | cursors current; local active count 670,808 versus Supabase 1,202,072; 2,566 companies differ, with 548,408 remote excess and 17,136 local excess active rows | confirmed severe stale-state drift |
| local PostgreSQL → Typesense | cursor current; local active count 670,808 versus Typesense 694,464 | confirmed smaller stale-state drift |

## Prioritized remediation organizer

The confirmed findings are tracked by [#5922](https://github.com/colophon-group/jobseek/issues/5922). All child issues are explicitly on hold.

| priority | issue | solution boundary |
|---|---|---|
| P0 | [#5923](https://github.com/colophon-group/jobseek/issues/5923) | private ingress and fleet SSH baseline |
| P0 | [#5928](https://github.com/colophon-group/jobseek/issues/5928) | PostgreSQL capacity and checkpoint headroom |
| P0 | [#5927](https://github.com/colophon-group/jobseek/issues/5927) | PostgreSQL backups and tested recovery |
| P1 | [#5925](https://github.com/colophon-group/jobseek/issues/5925) | protected credential delivery and later rotation |
| P1 | [#5930](https://github.com/colophon-group/jobseek/issues/5930) | deploy-safe reconciliation and drift repair |
| P1 | [#5926](https://github.com/colophon-group/jobseek/issues/5926) | full-fleet monitoring and correct alert ownership |
| P1 | [#5929](https://github.com/colophon-group/jobseek/issues/5929) | indexed daily annotation sampling |
| P2 | [#5924](https://github.com/colophon-group/jobseek/issues/5924) | fleet patch/reboot and immutable image lifecycle |

## Confirmed root-cause findings

### 1. Internet ingress and SSH controls do not match the private-service design

The crawler and PostgreSQL hosts have UFW disabled. Host-network containers bind on all interfaces. External probes reached the crawler metrics/Alloy endpoints and PostgreSQL. PostgreSQL listens on all addresses, has a broad password HBA entry, does not use TLS, and received 1,727 database authentication failures in 24 hours. SSH received approximately 16k/27k/19k failed attempts across crawler/PostgreSQL/Typesense in 24 hours. PostgreSQL also has an unlocked root password while sshd permits root and password login.

Root cause: private and operational services rely on intended topology rather than enforced provider/host firewall policy and narrow bind addresses; fleet SSH policy is not codified.

### 2. PostgreSQL has insufficient capacity and unproven recovery

The 20 GiB data volume is 92% full with about 1.8 GiB free. The database is about 16 GiB, dominated by the 14 GiB `descriptions` relation. Checkpoints are overwhelmingly requested rather than timed (51,106 versus 6,330), consistent with a 1 GiB WAL ceiling under sustained writes. There is no host-local `pg_dump`, base backup, WAL archive, replica, backup timer, or restore evidence. `archive_mode` and checksums are off. Repository docs claim volume snapshots, but neither their existence nor recovery usability could be verified.

Root cause: the authoritative datastore has no capacity/recovery SLO or automated evidence gate; its original 20 GiB sizing, 2 GiB container memory limit, and 1 GiB WAL ceiling have not evolved with the 16 GiB workload.

### 3. Downstream reconciliation is structurally prevented from running

The exporter sleeps 86,400 seconds before the first reconciliation. Ten crawler deployments occurred in roughly eight hours on the audit day; every deploy recreates the exporter and restarts that clock. Live Grafana reconciliation counters were zero for all container instances. The code also compares only total counts, samples 200/100 random rows, mutates sampled rows to repair them, and has no persisted last-success timestamp.

This allowed a current cursor to coexist with major historic field drift: Supabase reports roughly 531k more active postings than local PostgreSQL net, and Typesense reports roughly 24k more. A targeted sample of 20 Supabase-active Accenture rows found all 20 present but inactive locally, including changes weeks old.

Root cause: reconciliation lifecycle is coupled to a frequently recreated process, and its success/freshness is neither persistent nor alerted. Current cursors prove only incremental progress after the cursor, not full-store parity.

### 4. Monitoring excludes two hosts and one alert is continuously false-firing

Grafana Cloud contains filesystem telemetry only for the crawler host among the Hetzner fleet. PostgreSQL and Typesense cannot trigger the configured disk rule. `ExporterStale` is currently firing five false alerts because the gauge exists with value zero on browser, drain, and worker endpoints, while the rule does not select `instance="exporter"`; the real exporter series is current.

Root cause: metrics and rules are defined per crawler container without ownership selectors, and fleet monitoring was never deployed to the standalone database/search hosts.

### 5. Service credentials are exposed through process arguments

The Cloudflare Tunnel systemd unit is world-readable and embeds its token in `ExecStart`; an unprivileged account can read it through systemd metadata. The Typesense admin key is likewise present in server arguments visible through the process table.

Root cause: long-lived credentials are passed as command-line arguments instead of protected credential files/systemd credentials or root-only environment delivery.

### 6. Daily annotation sampling lacks its access-path index

The daily annotation run reached PostgreSQL but the 24-hour sample query timed out twice at the enforced 30-second statement timeout. `EXPLAIN` uses the partial active index to visit a large active set, filters by `first_seen_at`, then sorts; no index begins with `first_seen_at` for active rows. The final missing HuggingFace file is only the terminal symptom.

Root cause: the sampler query and production table growth were not paired with a supporting partial/composite index or a query-plan performance test.

### 7. Fleet patch and image provenance are not controlled end to end

All hosts require reboot. Pending package counts are 26/46/58, including security updates on crawler and Typesense. External services use mutable tags (`grafana/alloy:latest`, `redis:8-alpine`, `postgres:16-alpine`); Postgres and Typesense have no repo-owned host deployment/upgrade workflow.

Root cause: unattended upgrades, container pulls, reboot decisions, and service version promotion are separate ad hoc mechanisms with no fleet-wide maintenance window or immutable manifest.

## Healthy controls worth preserving

- The Codex runner account has no sudo/Docker access and cannot read the crawler environment file.
- Sensitive crawler env and authorized-key files have restrictive permissions.
- Typesense origin port is denied publicly and allowed only from the Hetzner private network; API endpoints enforce keys.
- Typesense aliases and collection inventory are internally consistent.
- Redis persistence currently succeeds and its no-eviction ceiling has headroom.
- Current crawler services are healthy, image-tagged, and the recent worker OOM root causes were fixed and production-verified rather than merely hidden by larger limits.
- CDC cursors for Supabase and Typesense are atomic, equal, and current; the remaining problem is historic/full-state parity.
- NTP is synchronized and no filesystem/kernel I/O errors were found in the retained 30-day journals.

## Evidence gaps that require operator/control-plane access

- Verify Hetzner project server ownership, provider firewall attachments, backups/snapshots, retention, and last successful snapshot.
- Verify a restore from an authoritative PostgreSQL backup and record RPO/RTO.
- Verify the documented Cloudflare per-IP rate-limit rule and notification routing in the Cloudflare control plane.
- Name accountable owners/on-call escalation paths for all three hosts.
- Verify notification delivery rather than rule evaluation only; the false `ExporterStale` alerts show that firing state alone is not useful evidence.

## Remediation state

No service was restarted, deployed, reconfigured, resized, patched, or mutated during this audit. Database queries and external probes were read-only. Issue creation is organizational only; remediation remains paused.

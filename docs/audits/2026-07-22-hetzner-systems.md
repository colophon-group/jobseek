# Hetzner systems audit — 2026-07-22

Status: baseline evidence collection is complete for the three known non-Murmur hosts. The operator subsequently approved staged remediation; the baseline matrices preserve the original observations, and the verified change log below records the evolving production state.

This report records read-only evidence. It omits public IP addresses, resource IDs, credentials, full connection strings, and authentication material. Murmur is excluded. Host inspection was supplemented with read-only Hetzner Cloud API evidence for servers, public/private networking, firewalls, Volumes, backups/snapshots, resource protection, SSH keys, and coarse provider CPU metrics. The project token does not expose account membership, billing ownership, or the Cloudflare control plane.

## Verified remediation change log

Evidence below is additive to the original audit snapshot. A control is not
called resolved until its issue acceptance criteria and rollback/removal gates
pass.

### 2026-07-22 — replacement data protection staged

- Provisioned a delete-protected private Hetzner BX11 Storage Box with seven
  daily secondary snapshots and separate home-isolated PostgreSQL and
  Typesense writer subaccounts. Credentials and resource identifiers remain
  outside the repository.
- Typesense completed an application-consistent Snapshot API backup and
  encrypted Restic upload without a process restart. The artifact was
  1,348,345,503 bytes and the run took about 164 seconds. A cross-host restore
  with verification restored all 1,348,345,503 bytes in 13 seconds; the
  temporary Typesense 27.1 node became healthy, loaded all seven collections
  and aliases, matched the six non-posting collection counts exactly, served
  representative document reads and a live search, and differed from the
  concurrently advancing production posting count only by newer writes.
- Captured and independently uploaded a transaction-consistent encrypted
  PostgreSQL logical checkpoint before storage changes. The attached XFS
  Volume was expanded online from 20 to 40 GiB; it now has about 22 GiB free.
- Built PostgreSQL 16.13 with checksum-pinned pgBackRest 2.59.0 and its
  upstream backup/restore smoke suite. Isolated tests reproduced a 60-second
  pgBackRest/libssh2 segmentation fault against the Storage Box on both the
  packaged 2.57.0 client and the source-built 2.59.0 client. The production
  design therefore uses a private SMB 3.1.1 mount with transport encryption,
  hard I/O semantics and CIFS symlink emulation, plus pgBackRest AES repository
  encryption. A filesystem fsync/checksum/rename/symlink probe and an isolated
  encrypted stanza-create/WAL/full-backup/restore/row-checksum drill passed.
- Replaced the PostgreSQL container with the pgBackRest-capable image using an
  automatic rollback script. The measured container handoff was about 0.2
  seconds. PostgreSQL now has a 4 GiB memory limit, 1 GiB shared buffers, a 4
  GiB WAL ceiling and continuous archiving. The initial full backup covered
  17.1 GiB, stored 7.7 GiB, completed in 199 seconds, and left the archive
  failure count at zero. A clean restore copied all 17.1 GiB in 251 seconds,
  replayed archived WAL to a writable new timeline, and passed `pg_amcheck`
  heap and B-tree parent verification across all 384 relations and 2,147,497
  pages. The restored database exposed 2,447,190 postings with zero rows
  failing the structural-null probe. Temporary drill state was removed. The
  reviewed revision was then merged and deployed from `main` to both data
  hosts. A workflow evaluation bug was reproduced and fixed: protected
  environment variables cannot be resolved while GitHub expands a job matrix,
  so host selection now occurs only inside runtime steps after the production
  environment is attached.
- The exact deployed PostgreSQL service completed a fresh differential backup
  covering 19.1 GB with a 4.27 GB encrypted repository delta in 140 seconds.
  The exact deployed Typesense service completed a 1,362,668,187-byte
  consistent snapshot, encrypted upload, retention prune, and repository check
  in 78 seconds. Both atomic status files and Prometheus textfiles report
  success. PostgreSQL stayed ready with zero archive failures; Typesense stayed
  healthy with its original start time, and neither container restarted or was
  OOM-killed.
- Both backup timers are enabled with their next jittered runs visible. Delete
  and rebuild protection is enabled on the PostgreSQL and Typesense servers,
  and delete protection is enabled on the PostgreSQL Volume. The stopped
  pre-cutover PostgreSQL container was removed after these gates passed.
- The legacy Hetzner OS backups remain unchanged. Their final removal is gated
  only on proving that backup failure/freshness evidence reaches the daily
  Codex error-review workflow and can create or update an actionable GitHub
  issue, as required by `docs/19-data-backup-recovery.md`.

### 2026-07-22 — fleet observability staged

- Merged and deployed the repo-owned host telemetry surface from `main` to the
  crawler, PostgreSQL, and Typesense hosts. Each runs the pinned Alloy binary
  as a dedicated unprivileged systemd user on a loopback-only listener, plus a
  root-owned read-only sampler timer. Live Grafana queries returned all three
  expected host and collector series and zero failed sampler probes.
- The first deployment exposed two rollout defects rather than hiding them:
  root-only config parents prevented the unprivileged service from reading its
  config, while a shared transitional listener let the crawler readiness probe
  reach the older Compose collector. The corrected installer uses group-only
  traversal, a distinct host port, main-PID/executable verification, and
  removal of first-install artifacts during rollback.
- Direct verification then found the hardened crawler Compose Alloy
  restart-looping because its existing WAL/cursor volume was owned by the
  deploy account while the root process had every capability dropped. The
  volume was stopped, normalized to root-owned mode `0700` with a pinned
  networkless helper, and only Alloy was restarted. Its loopback readiness and
  six crawler scrape targets recovered; workers, PostgreSQL, and Typesense were
  not restarted. The repo deploy now enforces this ownership contract and
  gates success on the Compose readiness endpoint.
- The production Mimir write correctly rejected 28 rules in one group because
  the tenant limit is 20 and restored the prior group. The source is now split
  into logical fleet and crawler groups (18 and 10 rules). Temporary live
  namespaces proved exact 28-rule activation, cleanup, and whole-namespace
  rollback after an intentionally invalid second group. Mimir's canonical
  `24h` to `1d` duration rewrite is normalized during otherwise exact
  verification. Production promotion of both groups subsequently passed, and
  #5926 was closed with live fleet coverage evidence.
- A follow-up retained-series check found that standard Unix-exporter and
  Alloy series were healthy on all three hosts but every sampler-produced
  `jobseek_*` family was absent from Grafana. A safe redeploy from then-current
  `main` reproduced the defect without restarting workloads: the Alloy Unix
  exporter configured a textfile directory but its explicit collector
  allowlist omitted `textfile`. The #5993 remediation deployment enabled that
  collector on all three hosts. Its new post-deploy Grafana gate verified fresh
  sampler, probe, container, backup, PostgreSQL-readiness, and
  Typesense-readiness series for every expected role before transactionally
  re-verifying the rules. Only Alloy restarted; crawler workloads, PostgreSQL,
  Typesense, and the tunnel were not restarted. The regression test and
  production evidence were merged, and #5993 is closed.

### 2026-07-23 — deploy-independent cross-store reconciliation verified

- Replaced the exporter's restart-sensitive daily sleep and probabilistic
  samples with a Hetzner-hosted systemd timer, a resource-capped read-only
  one-shot container, a shared host mutation lock, a PostgreSQL advisory lock,
  deterministic UUID partitions, independent durable cursors for Supabase and
  Typesense, aggregate run history, and fleet alert metrics. Crawler deploys,
  scheduled Typesense maintenance, and reconciliation now serialize without
  coupling reconciliation freshness to an exporter container lifetime.
- The production Supabase cycle checked all 256 partitions and 2,476,793 local
  postings. It detected and repaired 560,601 actionable discrepancies,
  including 544,611 active-state mismatches, and completed with zero
  unresolved. Remote-only inactive Supabase rows remain retained by design for
  downstream history and foreign-key consumers; active-state parity is the
  enforced contract.
- The initial Typesense cycle checked 2,477,149 local postings, repaired
  2,471,915 legacy documents that predated the deterministic bucket field, and
  completed with zero unresolved. The final two-pass full-collection stream
  found no invalid or local unbucketed documents, set bootstrap complete, and
  reset the durable cursor. Typesense was not restarted; its health remained
  green throughout the 1-CPU repair, and temporary disk growth stayed within
  the measured capacity envelope.
- The first full Typesense invocation reached its 50-minute safety cap after
  198 partitions in that invocation. Its last verified cursor was preserved,
  the one-shot container was removed, and the next invocation resumed at the
  exact next partition. That exercise exposed a run-ledger defect: the capped
  process ignored the registered shutdown event and left its row marked
  `running`. Crawler v0.13.185 now cancels the one-shot task on `SIGTERM`,
  records it as interrupted before unlocking, and lets every new advisory-lock
  holder immediately classify prior running rows as interrupted. Live
  acceptance reclassified the orphan as `InterruptedRun`; the next bounded run
  completed 32 partitions with zero detected, repaired, or unresolved rows.
- Two post-bootstrap scheduled/controlled slices each checked 16 partitions
  per target with zero actionable discrepancies. Both CDC legs reached zero
  lag, the timer remained deploy-independent across the v0.13.185 rollout, all
  crawler Compose containers remained free of restarts and OOM kills, and no
  maintenance container or advisory lock was left behind.
- A final stationary recovered-company check held the shared mutation lock,
  gracefully paused only crawler writers/browser/drain, allowed both exporter
  legs to reach zero lag, and compared exact active-ID sets without emitting
  IDs. Local PostgreSQL, Supabase, and Typesense matched bidirectionally for
  Capital One (1,698 each), ETH Zürich (118), G-Research (60), Hack The Box
  (19), Snyk (26), and the currently empty Exotec set (0). All paused services
  restarted healthy; Typesense and PostgreSQL were not restarted.
- The implementation and follow-up hardening were reviewed in #6053, #6054,
  and #6057. This closes the systemic reconciliation root cause in #5930 and
  supplies the final downstream acceptance evidence for #6016; it does not
  replace the normal continuous CDC path.

### 2026-07-23 — PostgreSQL shared-memory incident remediated

- The incident PostgreSQL container had been recreated with Docker's 64 MiB
  `/dev/shm` default despite retaining a 4 GiB memory cgroup, 1 GiB
  `shared_buffers`, 16 MiB `work_mem`, up to eight parallel workers and two
  workers per gather. The host root filesystem, host `/dev/shm`, RAM, and
  inodes had headroom; the failure boundary was inside the container.
- PostgreSQL recorded 31,398 `could not resize shared memory segment` ENOSPC
  errors since the current container started, including a peak of 1,283 errors
  in one minute. All crawler worker classes were affected. The
  container had no restart or OOM flag, separating this incident from host
  disk exhaustion and cgroup memory exhaustion.
- Both repo-owned live replacement paths omitted an explicit shared-memory
  size, so fixing only the current container would have been temporary. The
  reviewed root-cause change gives both paths a 1 GiB ceiling, validates the
  configured and mounted capacity, adds the contract to the redacted ingress
  audit, publishes configured/capacity/used/available metrics, and routes
  regression or sustained pressure to the daily Codex error review.
- Monitor and scrape tasks reschedule through Redis with a five-minute error
  backoff when the database path raises, so normal recovery is to preserve the
  queues and verify successful retries after the guarded container handoff,
  not to replay identifiers blindly.
- The reviewed change in #6059 merged and all automatic crawler, backup and
  observability deployments completed successfully. A protected ingress apply
  then passed the fresh-backup gate, replaced PostgreSQL with a measured 1 GiB
  configured and mounted `/dev/shm`, validated the real crawler private paths,
  and committed the database transaction. The exact deployed conformance probe
  reports the PostgreSQL listener, HBA, SCRAM, repository-owned network config,
  and shared-memory contract compliant. The host sampler series reached Grafana
  with the same 1 GiB configured/capacity values and the critical pressure
  alert remained inactive.
- The remaining fleet-ingress transaction did not falsely pass: a crawler SSH
  allowlist parser defect caused its commit conformance to fail, so crawler
  rollback succeeded and the provider-firewall tail was skipped. PostgreSQL
  remained committed with no pending transaction or rollback container. This
  outcome reopened #5923 and was not recorded as completion of the fleet
  ingress control until the independently verified follow-up below.
- A final 15-minute-48-second normal-load observation recorded zero PostgreSQL
  or crawler shared-memory ENOSPC errors, no PostgreSQL/worker/browser restart
  or OOM state, 3% `/dev/shm` use, 1.82 GiB of the 4 GiB memory cgroup in use,
  database readiness, and zero WAL archive failures. The workers completed
  1,659 monitor runs and 151 scrapes during the window. Ready work decreased
  from 817 to 779 simple tasks and from 271 to 254 browser tasks while the
  normal 60/7 inflight concurrency remained active; the pre-existing 3/1
  simple/browser dead-letter counts did not increase. These gates close #6055
  without manual task replay.

### 2026-07-23 — fleet ingress and SSH baseline verified

- The failed crawler commit was traced to the conformance parser treating
  OpenSSH's repeated `allowusers root` and `allowusers deploy` output as a
  scalar setting, overwriting the required root entry. The original stage also
  checked only a weaker subset of the final contract. #6061 unions only
  repeated allowlist entries, keeps every other security directive
  single-valued and exact, runs the same host-only conformance during staging,
  and requires full conformance before a staged host can advance.
- The retry path first proved that the already-hardened PostgreSQL data plane
  passed its exact listener, HBA, SCRAM, repository config, shared-memory and
  authentication contract. Protected apply run `30005417174` therefore skipped
  its container handoff, staged and committed all three host policies, proved
  the live crawler-to-PostgreSQL and crawler-to-Typesense private paths, then
  created and attached the provider firewall last. No rollback path was
  discarded before its corresponding commit.
- Direct post-apply inspection found no pending transaction, rollback
  container or rollback timer. All three hosts passed full redacted
  conformance: key-only SSH with an explicit allowlist limited to root/deploy,
  UFW default deny with exact role rules, loopback/private-only service
  listeners, a locked PostgreSQL root password, and the exact database policy.
  The provider firewall has only the reviewed SSH and ICMP ingress rules,
  covers exactly the three non-Murmur hosts, and has no unrelated managed
  sibling.
- External probes found SSH reachable on all three hosts and no sensitive
  public service or metrics port reachable. The independent read-only workflow
  run `30005562982` then passed every host and provider conformance job from the
  exact merged revision.
- PostgreSQL and Typesense retained their pre-apply start times, zero restart
  and OOM state, database readiness/zero WAL archive failures, origin health
  and public tunnel health. Crawler retained nine healthy containers, zero
  restarts, active normal queue concurrency, and unchanged 3/1
  simple/browser dead-letter counts. The retry therefore completed without a
  PostgreSQL or Typesense restart and closes #5923. The maintained policy,
  threat model and rollback sequence remain in
  [`16-hetzner-maintenance.md`](../16-hetzner-maintenance.md).

### 2026-07-23 — commit-safe CDC writer race verified

- The commit-safe CDC cutoff introduced by #6049/#6052 failed closed three
  times during a high-frequency PostgreSQL abort storm. The exporter recovered
  on its next tick and no skipped row was demonstrated, but the repeated
  `cdc_snapshot_cutoff.unknown_writer` error made the cursor protocol
  unavailable under exactly the load where it is most important.
- The holder was not an anonymous session or prepared transaction:
  `max_prepared_transactions` and the prepared-transaction count were both
  zero. A 30-second production sample observed 571 cutoff probes and active
  writers in 236 of them without reproducing an unidentified live holder.
  Correlated retained logs placed the failures inside the PostgreSQL
  shared-memory incident, while the cross-store reconciler was not running.
  The root cause was a race between PostgreSQL's dynamic `pg_locks` and
  `pg_stat_activity` views: a transaction could be captured in the first lock
  scan, commit or roll back, and disappear before its transaction start was
  read. The old query conflated that already-released holder with a still-held
  lock whose identity was genuinely unavailable.
- Crawler v0.13.188 materializes the initial lock set, scans activity, and then
  rechecks the exact PID and advisory-lock key. A holder with a transaction
  start remains part of the conservative cutoff. A holder that disappeared is
  classified as a benign released-writer race: its committed change is visible
  to the export statement that follows, while the strict cutoff still excludes
  stamps at or after the captured clock. A PID-less prepared holder or a
  session-level holder that remains locked still fails closed. Dedicated
  PostgreSQL E2E coverage forces a writer commit between the two scans and
  proves exactly-once export; a persistent session-lock test proves the unsafe
  case is still rejected. The complete crawler suite, PostgreSQL E2E, CI and
  CodeQL passed before the immutable image was deployed.
- The observability deployment added separate counters for benign released
  races and genuinely unidentified holders plus a high-severity
  `CdcWriterIdentityUnavailable` rule routed to the daily Codex error review.
  Production Grafana ingestion showed three natural released-holder races,
  zero unknown-writer increase and no firing alert. The exporter emitted the
  matching three structured release events with zero unknown-writer errors,
  tick errors or slow-cutoff warnings.
- Controlled full-cycle completion then verified both downstream stores under
  the repo-owned 1-CPU/1-GiB reconciler. Supabase's durable 256-partition cycle
  checked 2,478,703 local rows against 2,484,576 downstream rows, repaired five
  live active-state differences and ended with zero missing, remote-only
  active or unresolved rows; the 5,873-row difference is retained downstream
  inactive history by policy. Typesense's durable cycle checked an exact
  2,478,922 local and remote documents, repaired six live active-state
  differences and ended with zero missing, extra or unresolved documents.
  Both durable cursors reset cleanly and the hourly timer was restored.
- During a further 10-minute normal-load window the exporter completed 560
  ticks, stayed running with zero restart/OOM state, and handled three natural
  released-holder races without an unknown-writer or tick error. Directly
  sampled lag rose transiently to 42 rows during active write bursts and
  drained to an exact zero; retained metrics recorded a maximum cutoff delay
  of 18.1 seconds, below both the warning and alert thresholds. PostgreSQL and
  Typesense retained their prior start times, Typesense remained healthy, and
  no maintenance container, failed unit or stale reconciliation run remained.
  This closes the root cause and production acceptance criteria in #6034.

## Inventory and ownership

| host | role | platform | persistent data | deployment/owner surface |
|---|---|---|---|---|
| `jobseek-crawler-browser` (`jobseek-crawler` in Hetzner) | HTTP workers, browser worker, exporter, R2 drain, Redis, Alloy, Codex runners | Hetzner CX43, 8 shared vCPU, 16 GiB RAM, 160 GB root disk; Ubuntu 22.04; no host swap; Docker/Compose plus systemd | Redis Docker volume; Alloy position volume; `/srv/jobseek-codex` state, inputs, traces, and worktrees | repo-owned deploy paths exist and the resource has production/project/role labels; Hetzner backups are disabled and delete/rebuild protection is off; accountable human ownership remains implicit |
| `jobseek-postings-postgresql` | authoritative crawler PostgreSQL | Hetzner CX33, 4 shared vCPU, 8 GiB RAM, 80 GB root disk; Ubuntu 24.04; no swap; `postgres:16-alpine` with host networking | separate 20 GB XFS Hetzner Volume; PostgreSQL database is about 16 GiB | manually operated Docker container; mistaken daily OS backups are enabled but exclude the attached database Volume; delete/rebuild and Volume-delete protection are off; no labels, repo-owned host deploy, database backup job, or named service owner found |
| `jobseek-typesense` | Typesense and Cloudflare Tunnel | Hetzner CX23, 2 shared vCPU, 4 GiB RAM, 40 GB root disk; Ubuntu 24.04; no swap; `typesense/typesense:27.1` with host networking | `/mnt/typesense-data` on the root disk, about 1.8 GiB | manually operated Docker container and systemd `cloudflared`; seven mistaken daily OS backups incidentally include the data path, but no Typesense snapshot/archive or restore test exists; delete/rebuild protection is off and the resource has no ownership labels |

## Host coverage matrix

| control | crawler/Codex | PostgreSQL | Typesense | result |
|---|---|---|---|---|
| OS, kernel, packages, reboot | inspected | inspected | inspected | all three have `/var/run/reboot-required`; 26/46/58 packages pending; crawler and Typesense include security updates |
| SSH/access | effective sshd config, account locks, keys, isolation checked | effective sshd config, account status, keys checked | effective sshd config, account locks and keys checked | password authentication is enabled fleet-wide; PostgreSQL permits root login and its root password is set; Internet brute-force noise is substantial |
| ingress/firewall/private network | host listeners, UFW, external probes, public IPs, private-network attachment, provider firewall checked | host listeners, UFW, PostgreSQL bind/HBA, external probes, public IPs, private-network attachment, provider firewall checked | UFW, private port, public tunnel, DNS, external probes, public IPs, private-network attachment, provider firewall checked | the project has zero Hetzner Firewall resources; all three hosts have public IPv4 and IPv6 plus one shared private subnet; crawler and PostgreSQL have no active host firewall, and crawler operational endpoints and PostgreSQL are Internet-reachable; Typesense origin port is correctly private-only |
| Docker/service state | containers, limits, restarts, OOM history, image tags/digests checked | container state, memory, restart/OOM, config checked | container state, memory, restart/OOM, config checked | current services healthy; crawler OOM history predates the verified fixes in #5102/#5798/#5800 |
| CPU/RAM/swap/load/disk/inodes | host state plus seven-day provider CPU metrics | host state plus seven-day provider CPU metrics | host state plus seven-day provider CPU metrics | PostgreSQL data Volume is 91% full with about 1.7 GiB free; seven-day crawler CPU averaged 52.6% of capacity, p95 76.1%, peak 95.3%; other filesystems and inodes have headroom; no host has swap |
| backups/restore | Hetzner backups disabled; Redis is explicitly disposable; Codex SQLite state present; no host recovery test | seven mistaken daily server-root backups exist, but Hetzner does not include attached Volumes and the database lives entirely on the attached Volume; no snapshot, local backup/archive/replica, deletion protection, or restore evidence | seven mistaken daily OS backups incidentally include the rebuildable Typesense data path; no application-consistent snapshot/archive, independent retention, deletion protection, or restore evidence | neither service has the intended data-level backup; establish and restore-test PostgreSQL and Typesense backups before removing the OS backups |
| secrets/config permissions | deploy env is 0600; Codex runner cannot read env or Docker | Docker metadata is root-only; no deploy source-of-truth found | Cloudflare token and Typesense admin key are in process arguments visible to unprivileged local users | Codex isolation is good; Typesense host secret delivery must change and affected credentials must later be rotated |
| TLS/DNS | no public app endpoint in scope | PostgreSQL TLS disabled; private use intended but public listener exists | Cloudflare DNS/TLS valid; health public; API requires a key; documented rate limit not independently verifiable | tunnel edge is healthy; database exposure violates its intended private-network design |
| monitoring/alerts | Alloy and host exporter present; the Hetzner Codex error-review service has no supported historical metrics evidence path | absent | only indirect Typesense health/memory from crawler exporter | Grafana sees crawler host only; PostgreSQL and Typesense host disks/OS are absent; `DiskNearFull` cannot protect them; scheduled error review cannot query the retained metrics |
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
| daily error review | Hetzner systemd timer/service completed; root preflight bundle and lifecycle journal present, but the bundle has no metric time series and the restricted runner has no supported Grafana query path | operational but incomplete; tracked by #5948 |
| PostgreSQL | PostgreSQL 16.13; about 2.44M postings and 2.11M descriptions; descriptions relation about 14 GiB; 51,106 requested versus 6,330 timed checkpoints; `max_wal_size=1GB`; 2 GiB container limit; separate 20 GB Volume is 91% full and excluded from all seven Hetzner server backups | active capacity/performance and durability incident |
| Typesense | 27.1; health OK; seven aliases point to seven versioned collections; total posting documents differ from local PostgreSQL by only about 10 at observation; mistaken OS backups exist but no supported snapshot/archive workflow was found | structurally healthy, but `is_active` field drift, unsafe secret delivery, and unproven data recovery remain |
| Cloudflare Tunnel | active; public DNS resolves through Cloudflare; certificate valid; unauthenticated collections rejected | healthy edge; token delivery unsafe |
| refresh-typesense | scheduled workflow generally succeeds, with two recent failures among ten sampled runs | operating; reconciliation semantics are insufficient for field drift |
| IndexNow | scheduler intentionally retired in #2821; code/table remain | intentionally inactive, not an incident |
| local PostgreSQL → Supabase | cursors current; local active count 670,808 versus Supabase 1,202,072; 2,566 companies differ, with 548,408 remote excess and 17,136 local excess active rows | confirmed severe stale-state drift |
| local PostgreSQL → Typesense | cursor current; local active count 670,808 versus Typesense 694,464 | confirmed smaller stale-state drift |

## Severity-ranked remediation organizer

The confirmed findings are tracked by [#5922](https://github.com/colophon-group/jobseek/issues/5922). Production remediation is authorized and active; each mutation must retain its documented safety and rollback gates. Murmur remains out of scope.

Severity reflects impact if left unresolved; rank reflects the recommended execution order and dependencies. The existing repository severity taxonomy has high, medium, and low levels.

| rank | severity | issue | evidence-based reason and solution boundary |
|---:|---|---|---|
| 1 | high | [#5927](https://github.com/colophon-group/jobseek/issues/5927) | PostgreSQL has no data backup and Typesense lacks its intended application-consistent backup; establish and restore-test both replacement paths before removing the mistaken OS backups |
| 2 | high | [#5923](https://github.com/colophon-group/jobseek/issues/5923) | the project has no provider firewall, while PostgreSQL and operational endpoints are publicly reachable and fleet SSH permits passwords; enforce private ingress and key-only SSH |
| 3 | high | [#5928](https://github.com/colophon-group/jobseek/issues/5928) | the unbacked database Volume is 91% full with about 1.7 GiB free and sustained checkpoint pressure; expand and tune after minimum recovery evidence exists |
| 4 | high | [#5930](https://github.com/colophon-group/jobseek/issues/5930) | more than half a million stale active rows exist downstream while repeated deploys prevent reconciliation; make reconciliation deploy-independent, then repair and verify parity |
| 5 | high | [#5925](https://github.com/colophon-group/jobseek/issues/5925) | long-lived Typesense and Cloudflare credentials are recoverable by unprivileged local users; deploy protected delivery, then rotate |
| 6 | high | [#5926](https://github.com/colophon-group/jobseek/issues/5926) | monitoring missed the near-full database Volume and continuously false-fires exporter alerts; deploy full-fleet telemetry and ownership-correct alerts |
| 7 | medium | [#5948](https://github.com/colophon-group/jobseek/issues/5948) | the Hetzner error-review service can reach point-in-time localhost endpoints but has no supported historical metrics evidence path; add least-privilege bounded queries without weakening runner isolation |
| 8 | medium | [#5929](https://github.com/colophon-group/jobseek/issues/5929) | a missing production access-path index blocks daily annotation sampling but not the serving/crawling path; add the index/query guard and preserve the causal error |
| 9 | medium | [#5924](https://github.com/colophon-group/jobseek/issues/5924) | overdue reboots, mutable images, missing resource labels, and ad hoc host lifecycle accumulate risk but are not the current data-loss/availability trigger |

## Confirmed root-cause findings

### 1. Internet ingress and SSH controls do not match the private-service design

The crawler and PostgreSQL hosts have UFW disabled. Host-network containers bind on all interfaces. External probes reached the crawler metrics/Alloy endpoints and PostgreSQL. PostgreSQL listens on all addresses, has a broad password HBA entry, does not use TLS, and received 1,727 database authentication failures in 24 hours. SSH received approximately 16k/27k/19k failed attempts across crawler/PostgreSQL/Typesense in 24 hours. PostgreSQL also has an unlocked root password while sshd permits root and password login.

Hetzner control-plane evidence confirms that the project has no Firewall resources at all. Each in-scope host has both public IPv4 and IPv6 enabled and is connected to the same private subnet. The private network proves that a private path exists; it does not enforce use of that path or isolate listeners from the Internet.

Root cause: private and operational services rely on intended topology rather than enforced provider/host firewall policy and narrow bind addresses; fleet SSH policy is not codified.

### 2. PostgreSQL and Typesense lack their intended data backups; PostgreSQL also has insufficient capacity

The 20 GB data Volume is 91% full with about 1.7 GiB free. The database is about 16 GiB, dominated by the 14 GiB `descriptions` relation. Checkpoints are overwhelmingly requested rather than timed (51,106 versus 6,330), consistent with a 1 GiB WAL ceiling under sustained writes. There is no host-local `pg_dump`, base backup, WAL archive, replica, backup timer, or restore evidence. `archive_mode` and checksums are off.

The control plane contains seven daily backups bound to the PostgreSQL server, but they cover its 80 GB root disk only. The live database is on the separate 20 GB Volume, and [Hetzner explicitly excludes attached Volumes from server backups and snapshots](https://docs.hetzner.com/cloud/servers/backups-snapshots/overview/). There are no project snapshots. Delete/rebuild protection is disabled on the server and delete protection is disabled on the database Volume; bound backups are deleted with their server.

The Typesense host also has seven daily server backups. Its data path is on the root disk, so those artifacts incidentally include it, but the operator confirmed that OS backup was not the intended design for either service. No Typesense Snapshot API workflow, independently retained archive, or restore evidence exists. [Typesense's supported backup flow](https://typesense.org/docs/guide/backups.html) first creates a consistent snapshot through `POST /operations/snapshot`; the completed snapshot directory can then be archived and moved off-host. Archiving the active data directory directly is not a safe substitute.

Root cause: the provider's OS-backup switch was used as a proxy for application-data protection without an explicit data-boundary, consistency, retention, or restore contract. This left PostgreSQL completely unprotected and Typesense incidentally protected by the wrong artifact. Separately, the original 20 GB database sizing, 2 GiB container memory limit, and 1 GiB WAL ceiling have not evolved with the 16 GiB workload.

### 3. Downstream reconciliation is structurally prevented from running

The exporter sleeps 86,400 seconds before the first reconciliation. Ten crawler deployments occurred in roughly eight hours on the audit day; every deploy recreates the exporter and restarts that clock. Live Grafana reconciliation counters were zero for all container instances. The code also compares only total counts, samples 200/100 random rows, mutates sampled rows to repair them, and has no persisted last-success timestamp.

This allowed a current cursor to coexist with major historic field drift: Supabase reports roughly 531k more active postings than local PostgreSQL net, and Typesense reports roughly 24k more. A targeted sample of 20 Supabase-active Accenture rows found all 20 present but inactive locally, including changes weeks old.

Root cause: reconciliation lifecycle is coupled to a frequently recreated process, and its success/freshness is neither persistent nor alerted. Current cursors prove only incremental progress after the cursor, not full-store parity.

### 4. Monitoring excludes two hosts and one alert is continuously false-firing

Grafana Cloud contains filesystem telemetry only for the crawler host among the Hetzner fleet. PostgreSQL and Typesense cannot trigger the configured disk rule. `ExporterStale` is currently firing five false alerts because the gauge exists with value zero on browser, drain, and worker endpoints, while the rule does not select `instance="exporter"`; the real exporter series is current.

Hetzner's coarse seven-day CPU series adds capacity context but is not a substitute for service telemetry: crawler CPU averaged 52.6% of total capacity, reached p95 76.1%, and peaked at 95.3%; PostgreSQL averaged 14.7% and Typesense 5.0%. The provider API does not expose guest RAM, filesystem occupancy, PostgreSQL checkpoints, backup freshness for the attached Volume, or service ownership.

Root cause: metrics and rules are defined per crawler container without ownership selectors, and fleet monitoring was never deployed to the standalone database/search hosts.

### 5. The Hetzner Codex error-review service lacks historical metrics evidence

The daily error review is scheduled by `jobseek-codex-daily-error-review.timer` on the crawler host, not by GitHub Actions. A root `ExecStartPre` collects a redacted bundle, then the review runs as the restricted `codex-runner` account. The bundle covers host signals, selected container logs, cgroup state, and lifecycle journal entries, but not metric time series or bounded Grafana queries. The service receives no metrics-read credential or documented historical-query helper.

Live read-only checks as `codex-runner` reached application `/metrics` and Alloy self-telemetry on localhost. Those endpoints expose a current scrape, not the retained 24-hour data required by the routine; Alloy forwards telemetry to Grafana Cloud but is not the historical query store. Direct localhost reachability is also ambiguous because two Alloy-generation listeners are currently present.

Root cause: the runner was correctly isolated from Docker, sudo, and production environment files, but the root-owned evidence boundary was designed around logs and point-in-time host evidence and never extended with a least-privilege historical metrics contract. This is tracked by [#5948](https://github.com/colophon-group/jobseek/issues/5948).

### 6. Service credentials are exposed through process arguments

The Cloudflare Tunnel systemd unit is world-readable and embeds its token in `ExecStart`; an unprivileged account can read it through systemd metadata. The Typesense admin key is likewise present in server arguments visible through the process table.

Root cause: long-lived credentials are passed as command-line arguments instead of protected credential files/systemd credentials or root-only environment delivery.

### 7. Daily annotation sampling lacks its access-path index

The daily annotation run reached PostgreSQL but the 24-hour sample query timed out twice at the enforced 30-second statement timeout. `EXPLAIN` uses the partial active index to visit a large active set, filters by `first_seen_at`, then sorts; no index begins with `first_seen_at` for active rows. The final missing HuggingFace file is only the terminal symptom.

Root cause: the sampler query and production table growth were not paired with a supporting partial/composite index or a query-plan performance test.

### 8. Fleet patch and image provenance are not controlled end to end

All hosts require reboot. Pending package counts are 26/46/58, including security updates on crawler and Typesense. External services use mutable tags (`grafana/alloy:latest`, `redis:8-alpine`, `postgres:16-alpine`); Postgres and Typesense have no repo-owned host deployment/upgrade workflow.

Control-plane inventory also shows delete/rebuild protection disabled on all three servers, delete protection disabled on the database Volume and private network, no placement group, and no ownership labels on PostgreSQL or Typesense. The crawler is the only server with environment/project/role labels.

Root cause: unattended upgrades, container pulls, reboot decisions, resource protection/labeling, and service version promotion are separate ad hoc mechanisms with no fleet-wide maintenance window or immutable manifest.

## Healthy controls worth preserving

- The Codex runner account has no sudo/Docker access and cannot read the crawler environment file; preserve this isolation while adding bounded metrics evidence.
- Sensitive crawler env and authorized-key files have restrictive permissions.
- Typesense origin port is denied publicly and allowed only from the Hetzner private network; API endpoints enforce keys.
- Typesense aliases and collection inventory are internally consistent.
- Redis persistence currently succeeds and its no-eviction ceiling has headroom.
- Current crawler services are healthy, image-tagged, and the recent worker OOM root causes were fixed and production-verified rather than merely hidden by larger limits.
- CDC cursors for Supabase and Typesense are atomic, equal, and current; the remaining problem is historic/full-state parity.
- NTP is synchronized and no filesystem/kernel I/O errors were found in the retained 30-day journals.

## Reproducible control-plane evidence

The following were run with `hcloud` 1.66.0 and a project token supplied through `HCLOUD_TOKEN`. All are GET-only operations; token values, public addresses, resource IDs, and raw authentication material are not recorded in this report.

```bash
hcloud server list -o json
hcloud firewall list -o json
hcloud network list -o json
hcloud volume list -o json
hcloud image list --type backup -o json
hcloud image list --type snapshot -o json
hcloud primary-ip list -o json
hcloud load-balancer list -o json
hcloud floating-ip list -o json
hcloud placement-group list -o json
hcloud ssh-key list -o json
hcloud certificate list -o json
hcloud zone list -o json
hcloud server metrics <server> --type cpu --start <seven-days-ago> --end <now> -o json
```

Observed project inventory for the in-scope systems: three running servers, one private network, one attached 20 GB database Volume, six assigned primary IPs, one project SSH key, fourteen daily backup images across PostgreSQL and Typesense, zero Firewalls, zero snapshots, zero load balancers, zero floating IPs, zero placement groups, zero managed certificates, and zero DNS zones.

## Remaining evidence gaps

- Name and verify accountable human owners/on-call escalation paths; the Cloud project API does not expose project membership or billing ownership.
- Prove database/search backup failure and freshness evidence in the daily
  Codex issue-delivery path, then remove the mistaken Hetzner OS backups.
- Add explicit historical metrics coverage to the root-produced Codex error-review evidence boundary without exposing production or write credentials.
- Verify the documented Cloudflare per-IP rate-limit rule and notification routing in the Cloudflare control plane.
- Verify notification delivery rather than rule evaluation only; the false `ExporterStale` alerts show that firing state alone is not useful evidence.

## Remediation state

The baseline audit phase was read-only. Subsequent operator-approved changes
are recorded only in the verified remediation change log above so the original
observations remain reproducible. Replacement database/search backups,
restores, scheduling, capacity expansion, and resource protections are now
verified; legacy OS-backup removal remains gated on daily alert-delivery proof.

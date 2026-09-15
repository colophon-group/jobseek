# Daily error review - Codex-first ops routine

Codex-first routine for reviewing the last 24 hours of crawler errors on the
Hetzner box. It classifies errors against prior reviews, writes a dated report
to `~/dev/claude/review-jobseek-errors/`, deduplicates against GitHub issues,
and files or updates issues only for classes that are novel, regressing,
spiking, or incidents.

Prior exemplars (follow their shape): #2622, #2621, #2470, #2431.

## Invocation

- **Scheduled route:** Hetzner Codex runner through
  `jobseek-codex-daily-error-review.timer`. A root `ExecStartPre` collector
  writes a redacted read-only evidence bundle for the unprivileged
  `codex-runner` account, so the Codex process does not need Docker,
  `/home/deploy`, or production env access. Deployment settings and
  maintenance checks live in
  [18-codex-automation-deployment.md](18-codex-automation-deployment.md).
  Root-owned pre/post hooks also atomically record attempt, success, and
  in-progress state under `/srv/jobseek-codex/state/`; the independent host
  sampler publishes only numeric status fields. Mimir records failure,
  36-hour staleness, or a run stuck over three hours. Production email paging
  is disabled; see
  [production paging is disabled](16-hetzner-maintenance.md#production-paging-is-disabled).
  Every completed, failed, or timed-out Codex session (including subagents) is
  projected into the shared quality-gated trace dataset. The runner downloads
  and checksum-verifies every remote object before descriptor-anchored cleanup
  of matching local sources; failed verification retains them for retry.
- **Preferred manual route:** an operator-invoked Codex CLI session from the
  repo root, asking it to use the `jobseek-error-review` skill.
- **Manual traceable pilot:** run `codex exec --json` with the skill/runbook as
  the prompt and save the JSONL trace for agent trace collection checks.
- **Claude fallback:** `/jobseek-error-review` remains available through the
  legacy Claude Code slash command for compatibility.

## Runbook Source

Primary source of truth:
[`.agents/skills/jobseek-error-review/SKILL.md`](../.agents/skills/jobseek-error-review/SKILL.md).

The skill preserves the existing behavior from the legacy slash command:
read-only host inspection, prior-report memory, GitHub issue dedupe, evidence
collection, redaction, and filing only for `novel`, `regression`, `spike`, or
`incident` classes.

Incomplete evidence should reduce confidence, not silence actionable errors.
When a bundle has only a partial log window, the agent must caveat trends and
avoid unsupported 24-hour claims, but it may still file or update a GitHub
issue for a concrete, redacted, deduped error class visible in the observed
window.

An all-pool typed block from one origin has a bounded recovery path. Confirm it
with the production worker's actual browser entrypoint, allow one full origin
quarantine cooldown, and confirm it once more; then stop target probes. Never
solve or bypass the origin CAPTCHA. The operator should inspect the sanitized
replacement-capacity fields in `crawler proxy-audit` and use the explicit
dry-run/apply `crawler proxy-replace-webshare-pool` workflow documented in
`apps/crawler/AGENTS.md`. It fails closed if the dry run is stale, the current
pool changed, included capacity is insufficient, or runtime backbone
credentials do not match.

Njoyn inventory drift is classified separately from transport failure. A
successful `njoyn.snapshot.reconciled` event means both numbered passes were
structurally complete and their difference stayed inside the code-owned churn
budget; it is supporting evidence, not an error. `first_page_state_changed`,
`pagination_state_mismatch`, `empty_page_shape_mismatch`,
`page_count_mismatch`, `page_row_count_mismatch`,
`page_transition_did_not_converge`, `pass_*_exceeded`, and
`reconciliation_*_exceeded` are `inventory_unstable` failures and must remain
fail-closed. Do not rotate proxies for an inventory-stability failure unless
the same review window also proves a typed origin block on every configured
slot.

Host-memory classification is container-generation aware. The root collector
writes `host/docker-cgroup-memory.json` with Docker identity/timestamps and
cgroup-v2 memory counters. Reviews compare OOM and restart counters only for
the same pseudonymous `container_generation`; a sticky Docker
`OOMKilled=true` flag or a deployment that replaced the container is not, by
itself, a new daily incident. Raw Docker resource IDs do not enter the runner
bundle.

Container-exit classification is event-backed. The always-on root
`jobseek-codex-docker-lifecycle.service` filters out health-check noise,
allowlists non-secret lifecycle fields, and persists them in journald. The
bundle exports the requested window as `host/docker-lifecycle.jsonl`, so exit
codes, signals, OOM events, container identity, and replacement/restart timing
survive Docker's volatile event buffer and container recreation. The root
collector allowlists the journal again and one-way transforms raw IDs from
legacy schema rows before the bundle becomes readable by `codex-runner`.

Every file written to the runner bundle passes the same final redaction
boundary. In addition to credential shapes, it removes IPv4/IPv6 host
addresses, UUID-shaped domain/resource identifiers, and raw 64-hex
Docker/image identifiers. The 16-hex `container_generation` remains available
for same-generation comparisons without exposing the underlying resource ID.

Production maintenance attribution is deterministic rather than inferred from
container names. Repository-owned one-offs and the
`/usr/local/sbin/jobseek-maintenance` wrapper attach an all-or-nothing
operation, GitHub issue, reviewed revision, and runtime budget contract. The
watcher validates only those exact fields and ignores every other Docker
label. The root collector writes the derived, command-free
`host/maintenance-correlation.json` before the unprivileged review starts.

The deploy-independent reconciliation unit also writes through journald. The
bundle exports its exact review window as
`host/cross-store-reconciliation.log`, preserving the inner exception and
target after the short-lived Compose container has been removed. The same
size bound and final credential/address/resource-ID redaction apply before the
unprivileged runner can read it.

Redis capacity is collected at the same boundary. The bundle copies the
current `redis-capacity.prom` snapshot and exports the exact review window of
capacity-observer journal events as `host/redis-capacity-observer.log`.
Reviews must use Redis's configured `maxmemory` denominator from that snapshot,
not the larger container cgroup reported by `docker stats`. The manifest marks
the evidence incomplete when the capacity snapshot is unavailable, malformed,
more than eight hours old, unexpectedly future-dated, or the journal query
fails.

Disk incidents carry attribution rather than only a filesystem percentage.
The root collector records bounded `docker system df`, container writable-layer
sizes, image age/size inventory, exact sizes for the known Docker/containerd,
observability, Codex-runner, deploy, and log roots, the Docker GC timer's active,
substate, last-trigger, and next-trigger fields, and the exact-window
`jobseek-docker-gc.service` journal. The `disk_capacity.complete` manifest flag
fails closed if any command fails, times out, or reaches the bundle size cap.
These artifacts are diagnostic only: the unprivileged review must not turn a
large directory or image into removal authority.

Reviews must correlate synchronized service pauses with that file before
classifying instability:

- A single validated maintenance window owns only service pauses that overlap
  it or its bounded two-minute correlation edge.
- A single exact-overlap candidate takes precedence over windows that reach
  the pause only through that edge. Multiple exact candidates or multiple
  padding-only provenance contracts remain ambiguous.
- Service-pause correlation is limited to the eight monitored long-running
  crawler services: Redis, three HTTP workers, the browser worker, exporter,
  drain, and Alloy. Transient Compose init services cannot stretch a crawler
  maintenance window.
- Missing, partial, invalid, or conflicting provenance remains unattributed;
  never infer authorization from a name or an adjacent unlabelled one-off.
- Authorized maintenance is reported as a maintenance outcome, with its
  tracking issue, revision, downtime, termination mode, and restoration
  health, rather than as spontaneous worker instability.
- OOM/native exits, forced termination, nonzero maintenance one-offs, budget
  overruns, and failed restoration remain actionable even in an authorized
  window. The wrapper-owned marker's expected termination is not a failed
  maintenance one-off; marker OOM still is. Update the maintenance issue or
  create a deduplicated operational follow-up instead of reopening an
  unrelated instability issue.

Compatibility fallback:
[`.claude/commands/jobseek-error-review.md`](../.claude/commands/jobseek-error-review.md).
Keep it behaviorally aligned with the Codex skill when it is edited, but do
not treat Claude as the primary implementation path.

Do not spawn subagents by default. They are useful only for large independent
evidence sets and consume additional tokens; the main agent remains
responsible for classification, dedupe, redaction, and GitHub writes.
When the user explicitly requests parallel analysis, use the read-only
`jobseek-error-review-researcher` custom agent (GPT-5.6 Terra, high reasoning)
for each bounded evidence set.

## Implementation Verification

Use this rollout after adding or materially changing the routine:

1. **Read-only dry run:** collect host signals, logs, prior reports, and
   GitHub issue state; write the dated report; list would-file or would-update
   issues without creating, reopening, or commenting on GitHub issues.
2. **Production pilot:** run against the real host and real GitHub issue
   state, but file only when the criteria are clear and the evidence is
   already redacted.
3. **Two clean runs before normal filing:** require two consecutive runs where
   the report is written, known issues are deduped, forbidden commands are not
   used, and any GitHub write is justified by the classification rules.
4. **Stale wording scan:** after edits, scan docs and agent instructions for
   obsolete Claude-only, direct API billing, or old command wording.

## Sibling routines

- [15 — Daily labelled-postings routine](15-data-sampling-routine.md) — same
  scheduled-routine shape, data-collection surface instead of error surface.

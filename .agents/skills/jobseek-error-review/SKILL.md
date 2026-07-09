---
name: jobseek-error-review
description: Daily read-only review of Jobseek crawler errors on Hetzner; dedupe known issues, inspect GitHub issues, collect evidence, and file or update issues only for novel, regressing, spiking, or incident classes.
---

# Jobseek Error Review

Use this skill for the daily crawler error review. Operate from the repo
state and these instructions; do not rely on prior conversation context.

Prefer running this through the Hetzner local Codex runner or a local Codex
CLI session from the repository root. The Hetzner runner uses an isolated
worktree and a root-collected redacted evidence bundle, which keeps routine
output isolated from active development work while avoiding direct Docker or
deploy-env access from the Codex process. This routine should use
subscription-backed Codex execution where possible. Do not implement it as a
scheduled API-billed Codex GitHub Action.

The legacy Claude slash command at
`.claude/commands/jobseek-error-review.md` remains a compatibility fallback.
Keep behavior equivalent when using that fallback, but treat this skill as the
Codex-first runbook.

Do not spawn subagents by default. They cost extra tokens and are only useful
when the current run has large independent evidence sets and the user
explicitly asks for them. If subagents are used, keep them read-only and make
the main agent responsible for dedupe, classification, redaction, and all
GitHub write decisions.

## Mission

Scan the last 24 hours of errors on the crawler Hetzner box, classify each
error class against prior reviews and prior GitHub issues, write a dated
Markdown report, and file or update GitHub issues only for classes that meet
the filing criteria.

Model issue quality after prior examples #2622, #2621, #2470, and #2431.
Never mutate remote host state. GitHub writes are allowed only when the class
is novel, regressing, spiking, or an incident after dedupe.

## Target

- SSH: `deploy@116.203.192.19`
- Key: `~/.ssh/hetzner_deploy`
- Compose file reference: `/home/deploy/docker-compose.yml`

Long-running services. Collect logs with explicit `--since` and `--until`:

- `deploy-worker-1-1` - HTTP worker
- `deploy-worker-2-1` - HTTP worker
- `deploy-worker-3-1` - HTTP worker
- `deploy-browser-1-1` - Playwright/Lightpanda worker
- `deploy-exporter-1` - Postgres to Supabase and Typesense CDC
- `deploy-drain-1` - R2 description uploader
- `deploy-indexnow-1` - IndexNow notifier
- `deploy-redis-1` - local queue; only OOM and `level=error` lines
- `deploy-alloy-1` - log/metric shipper; only Alloy's own `level=error`

Ephemeral one-shots are exited containers created inside the review window
whose image matches `ghcr.io/*/jobseek-crawler:*`. Enumerate them with:

```bash
docker ps -a --filter status=exited --format '{{.Names}} {{.Image}} {{.CreatedAt}} {{.Status}}'
```

These include `crawler refresh-typesense`, `backfill-typesense`, `sync`,
`notify-indexnow`, and `reconcile` runs. Grab logs with `docker logs <id>`
before exited container logs age out. Ignore interactive debug containers
such as `tesla-debug`, `stupefied_hofstadter`, and `goofy_haibt`.

## Inputs

1. Use a window of the last 24 hours ending at UTC now, rounded down to the
   minute. Use explicit `--since` and `--until` values so log collection
   exactly matches the report header.
2. Read every `.md` report under
   `~/dev/claude/review-jobseek-errors/` before classifying. The directory
   name is legacy; keep using it for cross-run continuity unless a migration
   has already been completed.
3. Load prior GitHub issues for dedupe and pattern matching:

   ```bash
   gh issue list --label daily-error-review --state all --limit 200 \
     --json number,title,state,createdAt,body
   ```

4. Load recently merged PRs for regression correlation:

   ```bash
   gh pr list --state merged --limit 40 --json number,title,mergedAt
   ```

## Host Signals

Collect host signals before detailed log review. These commands are allowed
without sudo as `deploy`:

```bash
df -h /
df -h /var/lib/docker
free -h
uptime
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'
docker inspect --format '{{.Name}} OOMKilled={{.State.OOMKilled}} Status={{.State.Status}} RestartCount={{.RestartCount}} FinishedAt={{.State.FinishedAt}}' $(docker ps -aq)
dmesg -T 2>/dev/null | tail -n 500
journalctl -k --since "24 hours ago" --no-pager 2>/dev/null | tail -500
```

Use `journalctl -k` as a fallback if `dmesg` is restricted. If both kernel
log commands are restricted, note the gap under `## Host` and do not treat
that gap alone as an incident.

Flag an incident if any of these are true:

- `/` usage is at least 85%.
- `/var/lib/docker` usage is at least 85% when it is a separate mount.
- Any container was OOMKilled inside the window.
- Any container `RestartCount` incremented since yesterday's report.
- 15-minute load average is greater than CPU count x 2.
- Swap usage is greater than 50% of swap total.
- Kernel logs show `Out of memory: Killed process`, `I/O error`,
  `EXT4-fs error`, `Call Trace:`, or systemd OOM-killer entries.
- Any expected long-running service is missing from `docker ps`.

## Log Collection

For each long-running service:

```bash
docker logs --since "<ISO>" --until "<ISO>" <container> 2>&1
```

Parse structlog JSON where possible. Fall back to line matching only for
non-JSON lines. Extract `level`, `event`, `service_name`, exception class,
and a stable stack or message fingerprint.

For exited ephemeral containers inside the window:

```bash
docker logs <id> 2>&1 | tail -n 1000
```

Attribute those errors to a synthetic service derived from the command, such
as `refresh-typesense` or `notify-indexnow`.

## Classification

Group errors by `(service, exception class, stable message stem)`.

- `known`: appears in any prior daily report within 14 days. Count it but do
  not file.
- `novel`: absent from every prior report.
- `regression`: a known class that was absent for at least 3 consecutive days
  and is back today.
- `spike`: a known class whose 24-hour count is at least 3x its 7-day median.
  Require at least 3 prior days with non-zero signal.
- `incident`: host-signal trigger, unexpected zero logs from a long-running
  service for more than 1 hour, data loss or corruption signal, repeated OOM
  kills, or exporter/drain/indexnow stall with queue depth climbing and no
  progress.

## Report

Always write a report, even on a healthy day.

Path:

```text
~/dev/claude/review-jobseek-errors/YYYY-MM-DD.md
```

If today's file already exists, append a `## Rerun HH:MM UTC` section instead
of overwriting it.

Report schema:

```markdown
# Daily error review - YYYY-MM-DD
Window: YYYY-MM-DD HH:MM UTC -> YYYY-MM-DD HH:MM UTC

## Host
| metric | value |
|---|---|

## Totals
| service | info | warning | error |
|---|---:|---:|---:|

## Error classes (24h)
| class | service | count | status |
|---|---|---:|---|

## Details

## Filed issues

## Health
```

Under `## Details`, include every novel, regression, spike, or incident class:

- Error class and service.
- 24-hour count plus first-seen and last-seen UTC.
- One redacted sample log line.
- Brief root-cause hypothesis with 2 or 3 candidates. Use file paths only.
- Recent PR cross-references when correlation is plausible.

Under `## Filed issues`, list issue URLs filed or updated in this run, or
write `none`.

## GitHub Issues

File or update issues only for `novel`, `regression`, `spike`, or `incident`
classes.

Deduplicate first:

```bash
gh issue list --label daily-error-review --state all \
  --search "<error class stem or keyword>"
```

Match by error class and service, not exact title.

- If a matching open issue exists, add a comment with today's window, count,
  first-seen, and last-seen. Do not open a duplicate.
- If a matching closed issue from the last 30 days exists, reopen it with a
  comment explaining the recurrence.
- Otherwise open a new issue.

New issue template:

````markdown
## Summary

One paragraph with service, what broke, whether it is visible or silent, the
explicit 24-hour window, and suspected PR reference if any.

## Signal

5-day trend table (-4, -3, -2, -1, today) of count or batched log-line count.
Explain the unit.

## Sample log line

```json
{"level":"error","event":"redacted sample"}
```

## Root cause hypothesis

1. Candidate with file-path reference only and fix direction.
2. Candidate with file-path reference only and fix direction.

## Impact

User-facing consequence such as stale data, disabled boards, or silent
failure. If invisible beyond `level=error`, say so.
````

Title shape:

```text
[daily-error-review] <service>.<error_stem> (<trend>; <blast>)
```

For critical incidents, prefix the title with `[critical]`.

Labels are required:

- `daily-error-review`
- Exactly one of `error-review:critical`, `error-review:high`,
  `error-review:medium`, or `error-review:low`

Severity mapping:

- `error-review:critical`: active incident or data-loss risk.
- `error-review:high`: regression or full-service blast radius.
- `error-review:medium`: spike or partial-service regression.
- `error-review:low`: novel but contained.

## Redaction

The repository is public. Redact before any GitHub write:

- Full URLs with query strings: keep host and path shape only.
- IPs, port numbers, and hostnames beyond `jseek.co` or public CDNs.
- UUIDs, emails, user IDs, and company internal IDs.
- JWTs, cookies, API keys, and Authorization headers.
- SQL literal values; keep statement skeleton only.
- Request or response bodies beyond the exception message itself.
- Absolute paths containing `/home` or `/root`.
- Stack traces with parameter values. Keep exception class and innermost frame
  as `filename:function` only.

Local reports under `~/dev/claude/review-jobseek-errors/` are private. Raw
details belong there, not in GitHub issues.

## Guardrails

Allowed on the remote box:

- `docker logs`
- `docker ps`
- `docker inspect`
- `docker stats --no-stream`
- `df`
- `free`
- `uptime`
- `dmesg`
- `journalctl -k` when accessible
- Reading files under `/home/deploy/`
- Reading and writing temporary files under `/tmp/` that you created this run

Forbidden on the remote box:

- Restarts, stops, `docker exec`, `docker kill`, and any `docker compose`
  subcommand.
- `systemctl`.
- File edits outside `/tmp` temporary files you created this run.
- Deletions outside `/tmp` temporary files you created this run.
- Any `sudo`.
- Any outbound HTTP to third-party hosts.

Writing scripts to `/tmp` on the remote box is allowed only when the script is
idempotent and read-only. Remove scripts after use.

If a command's effect is not completely known to be read-only, stop, write the
intent to today's report, classify the gap as an incident, and do not run it.

## Escalation

For any incident:

1. File or update an issue labelled `daily-error-review` and
   `error-review:critical`.
2. Put `[critical]` at the start of a new critical issue title.
3. Append a line to
   `~/dev/claude/review-jobseek-errors/ALERTS.md`:

   ```text
   YYYY-MM-DD HH:MM UTC  <one-sentence>  <issue URL>
   ```

4. Do not attempt to fix production during this routine.

## Verification Rollout

Use this rollout when changing the routine or moving it to a new automation
surface:

1. Read-only dry run: collect host signals, logs, prior reports, and GitHub
   issue state; write the report; list would-file or would-update issues
   without creating, reopening, or commenting on GitHub issues.
2. Production pilot: run against the real host and real GitHub issue state,
   but file only when the criteria are unambiguous and the evidence is already
   redacted.
3. Require two clean runs before normal filing behavior. A clean run means the
   report is written, known issues are deduped, no forbidden command is used,
   and any GitHub write is justified by the classification rules.
4. After edits, scan docs and agent instructions for stale primary-runtime
   wording such as Claude-only phrasing, direct API billing, or obsolete
   command names.

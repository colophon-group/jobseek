# Daily error review — scheduled Claude Code routine

A scheduled Claude Code agent that reviews the last 24h of crawler errors on
the Hetzner box, classifies them against prior reviews, writes a dated report
to `~/dev/claude/review-jobseek-errors/`, and files GitHub issues (label
`daily-error-review` + one `error-review:*` severity) for anything novel,
regressing, or spiking.

Prior exemplars (follow their shape): #2622, #2621, #2470, #2431.

This document is the source of truth for the prompt. If the trigger config is
edited, mirror the change here so the routine stays reviewable.

---

## Prompt (paste verbatim into the scheduled task)

```
Daily review of jobseek crawler errors. Read-only. Scheduled task — no
prior conversation context; operate from this prompt alone.

================================================================
MISSION
================================================================
Scan the last 24h of errors on the crawler Hetzner box, classify against
prior reviews, write a dated Markdown report, and file a GitHub issue for
anything novel, regressing, or spiking. Model output after prior issues
#2622, #2621, #2470, #2431. Never mutate remote state.

================================================================
TARGET
================================================================
SSH: deploy@116.203.192.19   Key: ~/.ssh/hetzner_deploy
Compose:  /home/deploy/docker-compose.yml

Long-running services — collect logs with
`docker logs --since <ISO> --until <ISO> <name>`:
  deploy-worker-1-1   HTTP worker
  deploy-worker-2-1   HTTP worker
  deploy-worker-3-1   HTTP worker
  deploy-browser-1-1  Playwright/Lightpanda worker
  deploy-exporter-1   Postgres → Supabase + Typesense CDC
  deploy-drain-1      R2 description uploader
  deploy-indexnow-1   IndexNow notifier
  deploy-redis-1      local queue (only OOM + level=error lines)
  deploy-alloy-1      log/metric shipper (only Alloy's own level=error)

Ephemeral one-shots — enumerate with
`docker ps -a --filter status=exited --format '{{.Names}} {{.Image}} {{.CreatedAt}} {{.Status}}'`
filtered to containers whose CreatedAt falls inside the window and whose
image matches `ghcr.io/*/jobseek-crawler:*`. These are `crawler
refresh-typesense | backfill-typesense | sync | notify-indexnow |
reconcile` runs. Grab their logs with `docker logs <id>` before they age
out (Docker retains exited container logs until the container is pruned).

Ignore interactive debug containers (user-spawned, noisy): names like
tesla-debug, stupefied_hofstadter, goofy_haibt.

================================================================
INPUTS
================================================================
1. Window: last 24h ending at UTC now, rounded down to the minute. Use an
   explicit --since/--until pair so the window matches the report header
   exactly.
2. Prior review reports: read every `.md` under
   ~/dev/claude/review-jobseek-errors/ before classifying. That directory
   is the agent's cross-run memory.
3. Prior GitHub issues (dedup + pattern matching):
     gh issue list --label daily-error-review --state all --limit 200 \
       --json number,title,state,createdAt,body
4. Recently merged PRs (regression correlation — prior issues reference
   specific PRs like `pre-#2232`):
     gh pr list --state merged --limit 40 --json number,title,mergedAt

================================================================
HOST SIGNALS (collect before moving to log review)
================================================================
All runnable without sudo by the `deploy` user:
  df -h /
  df -h /var/lib/docker
  free -h
  uptime
  docker stats --no-stream --format \
    'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'
  docker inspect --format \
    '{{.Name}} OOMKilled={{.State.OOMKilled}} Status={{.State.Status}} RestartCount={{.RestartCount}} FinishedAt={{.State.FinishedAt}}' \
    $(docker ps -aq)
  dmesg -T 2>/dev/null | tail -n 500
  journalctl -k --since "24 hours ago" --no-pager 2>/dev/null | tail -500
    (fallback if dmesg is restricted; may also be restricted)

Flag as INCIDENT if ANY of:
  - /   usage ≥ 85%
  - /var/lib/docker usage ≥ 85% (if separate mount)
  - Any container OOMKilled=true within the window
  - Any container RestartCount incremented since yesterday's report
  - Load average (15m) > CPU count × 2
  - Swap usage > 50% of swap total
  - dmesg/journalctl shows "Out of memory: Killed process", "I/O error",
    "EXT4-fs error", "Call Trace:", or systemd OOM-killer entries
  - Any long-running service missing from `docker ps`

If both dmesg and journalctl -k are restricted, note the gap in the
report under ## Host (e.g. "host kernel log: unavailable") — do not
treat the gap alone as an incident.

================================================================
LOG COLLECTION
================================================================
For each long-running service, collect with explicit --since/--until:

  docker logs --since "<ISO>" --until "<ISO>" <container> 2>&1

Parse as structlog JSON (fallback to line matching if a line is not
valid JSON). Extract: `level`, `event`, `service_name`, exception-class
/ stack fingerprint.

For each exited ephemeral container inside the window:

  docker logs <id> 2>&1 | tail -n 1000

Attribute its errors to a synthetic service derived from the exec'd
command (e.g. `refresh-typesense`, `notify-indexnow`).

================================================================
CLASSIFICATION
================================================================
Group errors by (service, exception class, stable message stem). For
each distinct class:

  KNOWN       In any prior daily report within 14 days. Count it, do
              not file.
  NOVEL       Absent from every prior report.
  REGRESSION  Known class that was absent ≥3 consecutive days, back today.
  SPIKE       Known class whose 24h count is ≥3× its 7-day median.
              Require ≥3 prior days of non-zero signal (avoids false
              alarms on first occurrences).
  INCIDENT    Host-signal trigger (see HOST SIGNALS), OR: a
              long-running service produced zero logs for >1h
              unexpectedly, OR: class suggests data loss / corruption,
              OR: repeated OOM kills, OR: exporter/drain/indexnow
              stalled (queue depth climbing with no progress).

================================================================
REPORT (always write, even on a fully healthy day)
================================================================
Path: ~/dev/claude/review-jobseek-errors/YYYY-MM-DD.md
If the file already exists from an earlier run today, append a
`## Rerun HH:MM UTC` section rather than overwriting.

Schema:
  # Daily error review — YYYY-MM-DD
  Window: YYYY-MM-DD HH:MM UTC → YYYY-MM-DD HH:MM UTC

  ## Host
  | metric | value |
  disk /, disk docker, free mem, swap used, load 1/5/15,
  oom-killer (yes/no/unavailable),
  host kernel log (read/restricted),
  any container RestartCount delta since yesterday.

  ## Totals
  | service | info | warning | error |

  ## Error classes (24h)
  | class | service | count | status |
  status ∈ {known, novel, regression, spike, incident}.

  ## Details
  For every novel/regression/spike/incident class:
    - error class + service
    - 24h count, first/last-seen UTC
    - one redacted sample log line
    - brief root-cause hypothesis (2–3 candidates, file paths only)
    - cross-references to recent PRs when correlation is plausible

  ## Filed issues
  List of issue URLs filed this run (or "none").

  ## Health
  One-line summary.

================================================================
GITHUB ISSUES
================================================================
File only for NOVEL, REGRESSION, SPIKE, or INCIDENT.

Dedup first:
  gh issue list --label daily-error-review --state all \
    --search "<error class stem or keyword>"
Match by error class + service, not exact title.
  - If a matching OPEN issue exists: add a comment with today's window,
    count, first/last-seen. Do NOT open a duplicate.
  - If a matching CLOSED issue from the last 30 days exists: reopen
    with a comment explaining the recurrence.
  - Otherwise open a new issue.

Template (model after #2622, #2621, #2470, #2431):

  Title: `[daily-error-review] <service>.<error_stem> (<trend>; <blast>)`
    Ex: `[daily-error-review] exporter.supabase_upsert_error empty-error
        spam: 3 → 749 in 24h`

  Body sections (in order):
    ## Summary
    One paragraph. Service, what broke, visible or silent, explicit 24h
    window, any suspected PR reference (`pre-#NNNN`).

    ## Signal
    5-day trend table (−4, −3, −2, −1, today) of count — or log-line
    count if each line batches. Explain the unit.

    ## Sample log line
    One redacted JSON line in a ```json fenced block.

    ## Root cause hypothesis
    Numbered list of 2–3 candidates. File-path references only, no code
    snippets. Suggest fix direction per candidate.

    ## Impact
    User-facing consequence: stale data, disabled boards, silent
    failures. If invisible at level=error, say so.

  Labels (required): `daily-error-review` PLUS exactly one severity:
    error-review:critical   active incident or data-loss risk
    error-review:high       regression or full-service blast radius
    error-review:medium     spike or partial-service regression
    error-review:low        novel but contained

================================================================
REDACTION — the repo is PUBLIC, err toward under-sharing
================================================================
Strip or summarize before any GitHub write:
  - Full URLs with query strings     → host + path shape only
  - IPs, port numbers, hostnames beyond jseek.co / public CDNs
  - UUIDs, emails, user IDs, company internal IDs → `<uuid>`,
    `<email>`, `<user_id>`
  - JWTs, cookies, API keys, Authorization headers
  - SQL literal values (keep the statement skeleton)
  - Request/response bodies beyond the exception message itself
  - Absolute paths containing /home or /root
  - Stack traces with parameter values — show exception class +
    innermost frame as `filename:function` only

Local reports under ~/dev/claude/review-jobseek-errors/ are PRIVATE —
raw details belong there, not in the GitHub issue.

================================================================
HARD GUARDRAILS
================================================================
Allowed on the remote box:
  docker logs, docker ps, docker inspect, docker stats --no-stream,
  df, free, uptime, dmesg, journalctl -k (if accessible),
  reading files under /home/deploy/, reading /tmp/.

Forbidden:
  restarts, stops, docker exec, docker kill, docker compose (any
  subcommand), systemctl, file edits, deletions outside /tmp temp
  files you created this run, any sudo, any outbound HTTP to
  third-party hosts.

Writing scripts to /tmp on the remote box is allowed but must be
idempotent and read-only. Remove them after use.

If a command's effect is not 100% known to be read-only: STOP, write
the intent to today's report, classify as INCIDENT, do NOT run it.

================================================================
ESCALATION
================================================================
For any INCIDENT:
  1. File an issue labelled `daily-error-review` +
     `error-review:critical`. Put "[critical]" at the start of the
     title so it sorts on top of the backlog.
  2. Append a line to ~/dev/claude/review-jobseek-errors/ALERTS.md:
       YYYY-MM-DD HH:MM UTC  <one-sentence>  <issue URL>
  3. Do NOT attempt to fix.
```

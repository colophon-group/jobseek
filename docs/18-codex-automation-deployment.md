# Codex Automation Deployment

This document is the repo-owned deployment and maintenance spec for recurring
Codex runs. Codex app automation records, local app TOML files, Hetzner
systemd units, and governor state are deployment artifacts; do not treat them
as source of truth and do not commit local Codex app state.

## Automation Registry

| automation | cadence | execution | source of truth | model policy |
|---|---:|---|---|---|
| `jobseek-daily-classifications` | daily, 08:00 UTC | Hetzner crawler host, dedicated `codex-runner` user, local Codex CLI, isolated worktree per day | [15-data-sampling-routine.md](15-data-sampling-routine.md), [`.agents/skills/jobseek-label-daily/SKILL.md`](../.agents/skills/jobseek-label-daily/SKILL.md) | strongest orchestrator; task-sized labeller subagents |
| `jobseek-daily-error-review` | daily, 09:00 UTC | Hetzner crawler host, dedicated `codex-runner` user, local Codex CLI, root-collected redacted evidence bundle | [14-error-review-routine.md](14-error-review-routine.md), [`.agents/skills/jobseek-error-review/SKILL.md`](../.agents/skills/jobseek-error-review/SKILL.md) | strongest model, high reasoning; no default subagents |
| `jobseek-company-request-resolver` | self-regulated, checked every 15-30 min | Hetzner crawler host, dedicated `codex-runner` user, local Codex CLI, isolated worktree per issue | [01-agent-workflow.md](01-agent-workflow.md), `apps/crawler/AGENTS.md`, `ws task --issue <N>` | strongest orchestrator; task-sized `ws` subagents |

The recurring company resolver and daily routines must not be triggered by
GitHub Actions. They run on the Hetzner crawler host through local Codex CLI
auth so they can use the subscription-backed Codex surface where possible.

## Harness Invariants

These rules must hold whether the run is launched by the Codex desktop app,
Codex CLI, the Hetzner governor, or a future Codex scheduler.

- The automation prompt must be self-contained. It cannot rely on the
  conversation that created or updated the automation.
- The repo docs and skills above are the behavioral source of truth. Update
  them first, then update the deployed automation prompt or runner prompt.
- Do not install or invoke Claude Code from Codex automations. Do not add
  Codex GitHub Actions for these Hetzner-owned routines.
- Do not add a GitHub Actions trigger for the recurring company resolver or
  daily Codex routines.
- Run Hetzner Codex routines under a dedicated local user with no sudo,
  no Docker group, no production crawler environment, and no read access to
  crawler `.env` files.
- Treat `~/.codex/auth.json`, GitHub auth, and HuggingFace auth as password
  material. Do not print, upload, commit, or include them in traces.
- Keep Claude-compatible files only as migration fallbacks. When a fallback is
  edited, keep behavior aligned with the Codex-first source.
- Keep the main orchestration run on the strongest available Codex model with
  high reasoning for production routines.
- Use smaller Codex models only for bounded subagent tasks. Escalate an
  individual subagent attempt when validation fails repeatedly or evidence is
  ambiguous.
- Subagent contracts are harness-invariant: task name, rendered input path,
  output path, schema, and validator define the boundary. Harness-specific
  agent files may vary, but they must not fork prompts or schemas.
- Every run must be idempotent. Re-running the same date or issue must not
  duplicate HuggingFace rows, GitHub issues, GitHub PRs, or active `ws` claims.
- Every run must report what it did, what it skipped, and what requires human
  escalation.
- Secrets and local paths are deployment configuration. Do not write secrets
  to the repo, reports, traces, GitHub comments, or PR bodies.
- The unofficial ChatGPT usage endpoint probe is best-effort telemetry. The
  governor may use it to make better scheduling decisions, but it must fall
  back to local Codex JSONL usage accounting and conservative run budgets.

## Deployment Procedure

Use this process when creating or changing a Codex app automation or Hetzner
runner prompt:

1. Update the routine source doc or skill in this repo.
2. Build a self-contained automation prompt that tells Codex to read the
   source doc or skill from the checked-out repo and execute exactly one run.
3. Set the working directory to the Jobseek repo root. For Git-repo
   background worktrees, verify required untracked files and local secrets are
   visible to that execution environment before enabling the schedule.
4. Set the orchestrator to the strongest available Codex model and high
   reasoning.
5. For the classification routine, configure subagents by task size:
   normalizer and splitter can use smaller models when straightforward;
   extraction should use a stronger model by default and escalate on repeated
   validation failures.
6. Run a manual smoke pass. Prefer dry-run or small-count modes until the
   routine has two clean production runs.
7. Confirm the durable output: HuggingFace date rows, daily error report and
   issue updates, or company resolver PR.

Do not hand-edit local Codex automation TOML unless recovering from a broken
app state. If hand recovery is necessary, copy the final settings back into
this document or the relevant routine source in the same PR.

## Hetzner Codex Runner Implementation Plan

The company resolver and daily routines run as local Codex jobs on the crawler
machine. The current crawler host has enough headroom for one low-priority
Codex routine at a time, but the runner must be isolated so it cannot consume
production crawler secrets or Docker capacity.

### Phase 0 - committed test artifacts

- Keep [`../scripts/codex-usage-probe.py`](../scripts/codex-usage-probe.py) as
  a small, explicit probe for `https://chatgpt.com/backend-api/wham/usage`.
- The probe reads the Codex OAuth access token from `~/.codex/auth.json` or
  from `CODEX_USAGE_BEARER_TOKEN`, sends `chatgpt-account-id` when available,
  and prints only normalized usage windows.
- On macOS framework Python, pass `--ca-file /private/etc/ssl/cert.pem` if the
  Python install has no CA bundle. Hetzner system Python should use its normal
  CA store.
- Keep the probe out of hard correctness paths. If it returns a transport
  error, 401, 403, or an unexpected schema, the governor records the failure
  and uses fallback accounting.

### Phase 1 - host isolation

Create a dedicated local account, for example `codex-runner`:

- no sudo privileges
- not a member of the `docker` group
- no access to `/home/deploy/.env`, crawler `.env` files, Redis, Postgres,
  Typesense admin keys, or the Docker socket
- own home directory for `~/.codex/auth.json`, `~/.config/gh`, and narrow
  HuggingFace auth if trace upload is enabled
- repo state under `/srv/jobseek-codex`, writable only by `codex-runner`

Expected filesystem layout:

```text
/srv/jobseek-codex/
  repo/                 # normal checked-out clone tracking origin/main
  worktrees/            # one throwaway worktree per issue/run
  traces/               # CODEX_EXEC_JSONL output, not committed
  state/ledger.sqlite   # governor decisions, usage, claims, run outcomes
  logs/                 # optional sanitized summaries; journald is primary
  inputs/               # root-collected redacted evidence bundles
  data/                 # labeller data root for daily annotation outputs
```

Install runtime tools in user-owned paths where possible: `git`, `gh`, Codex
CLI, Python, `uv`, Node/pnpm only if the `ws` flow or tests require them. The
runner also needs the normal crawler browser/rendering stack: `libcairo2`,
`librsvg2-bin`, Playwright system dependencies, and Chromium installed into the
`codex-runner` browser cache. Do not grant write access to production crawler
deployment directories.

Provision Git identity and interactive auth as the runner user:

```bash
sudo -iu codex-runner
git config --global user.name "Jobseek Codex Runner"
git config --global user.email "codex-runner@colophon-group.org"
codex login --device-auth
gh auth login
exit
```

If trace upload is enabled, provision the narrow HuggingFace token in the
runner user's local HuggingFace cache, not in the systemd environment. The
Codex subprocess environment intentionally strips `HF_TOKEN`, so `ws task
complete` reads the token through `huggingface_hub` local auth while traces
remain free of deployment secrets.

```bash
sudo -iu codex-runner
mkdir -p ~/.cache/huggingface
umask 077
read -rsp 'HuggingFace token: ' HF_TOKEN_INPUT
printf '\n'
printf '%s' "$HF_TOKEN_INPUT" > ~/.cache/huggingface/token
unset HF_TOKEN_INPUT
test -s ~/.cache/huggingface/token
exit
```

Install rendering and browser support:

```bash
apt-get install -y libcairo2 librsvg2-bin
sudo -iu codex-runner
cd /srv/jobseek-codex/repo/apps/crawler
uv sync
.venv/bin/python -c \
  'from huggingface_hub.utils import get_token; raise SystemExit(0 if get_token() else 1)'
exit
/srv/jobseek-codex/repo/apps/crawler/.venv/bin/python -m playwright install-deps chromium
sudo -iu codex-runner
cd /srv/jobseek-codex/repo/apps/crawler
.venv/bin/python -m playwright install chromium
exit
```

### Phase 2 - network and process limits

Run the company resolver through `jobseek-codex-governor.service` and
`jobseek-codex-governor.timer`. Run the daily routines through
`jobseek-codex-daily-annotations.{service,timer}` and
`jobseek-codex-daily-error-review.{service,timer}`.

Committed deployment templates:

- [`../deploy/systemd/jobseek-codex-governor.service`](../deploy/systemd/jobseek-codex-governor.service)
  - `Type=oneshot`, low-priority CPU/IO scheduling, `CPUQuota=200%`,
  `MemoryHigh=3G`, `MemoryMax=4G`, `TasksMax=1024`, `ProtectSystem=strict`,
  and writable paths limited to `/srv/jobseek-codex` and
  `/home/codex-runner`.
- [`../deploy/systemd/jobseek-codex-governor.timer`](../deploy/systemd/jobseek-codex-governor.timer)
  - starts 2 minutes after boot, then 1 minute after the previous service
  finishes with up to 30 seconds of jitter.
- [`../deploy/systemd/jobseek-codex-daily-annotations.service`](../deploy/systemd/jobseek-codex-daily-annotations.service)
  - runs one daily labelled-postings routine from an isolated worktree, with
    DB access limited to `/etc/jobseek-codex/labeller.env`.
- [`../deploy/systemd/jobseek-codex-daily-annotations.timer`](../deploy/systemd/jobseek-codex-daily-annotations.timer)
  - starts once per day at 08:00 UTC with jitter and no missed-run catch-up.
- [`../deploy/systemd/jobseek-codex-daily-error-review.service`](../deploy/systemd/jobseek-codex-daily-error-review.service)
  - root `ExecStartPre` collects a redacted read-only Docker/host evidence
    bundle, then Codex analyzes that bundle without Docker or deploy-shell
    access.
- [`../deploy/systemd/jobseek-codex-daily-error-review.timer`](../deploy/systemd/jobseek-codex-daily-error-review.timer)
  - starts once per day at 09:00 UTC with jitter and no missed-run catch-up.
- [`../deploy/systemd/jobseek-codex-governor.env.example`](../deploy/systemd/jobseek-codex-governor.env.example)
  - non-secret governor defaults, including conservative budgets and usage
  thresholds.
- [`../deploy/systemd/jobseek-codex-labeller.env.example`](../deploy/systemd/jobseek-codex-labeller.env.example)
  - shape of the secret read-only local Postgres DSN used by annotation
  sampling and preparation.
- [`examples/company-resolver-codex-prompt.md`](examples/company-resolver-codex-prompt.md)
  - self-contained prompt template used by the governor for a single issue.

Install the unit and config as root, but keep the repo and run state owned by
`codex-runner`:

```bash
id -u codex-runner >/dev/null 2>&1 || \
  useradd --system --user-group --create-home \
    --home-dir /home/codex-runner --shell /bin/bash codex-runner
getent group docker >/dev/null 2>&1 && gpasswd --delete codex-runner docker || true

install -d -o codex-runner -g codex-runner -m 0750 /srv/jobseek-codex
install -d -o codex-runner -g codex-runner -m 0700 \
  /srv/jobseek-codex/worktrees \
  /srv/jobseek-codex/traces \
  /srv/jobseek-codex/state \
  /srv/jobseek-codex/logs \
  /srv/jobseek-codex/data/postings-labelled
install -d -o root -g codex-runner -m 0750 /srv/jobseek-codex/inputs
install -d -o root -g codex-runner -m 0750 /etc/jobseek-codex

install -o root -g root -m 0644 deploy/systemd/jobseek-codex-governor.service \
  /etc/systemd/system/jobseek-codex-governor.service
install -o root -g root -m 0644 deploy/systemd/jobseek-codex-governor.timer \
  /etc/systemd/system/jobseek-codex-governor.timer
install -o root -g root -m 0644 deploy/systemd/jobseek-codex-daily-annotations.service \
  /etc/systemd/system/jobseek-codex-daily-annotations.service
install -o root -g root -m 0644 deploy/systemd/jobseek-codex-daily-annotations.timer \
  /etc/systemd/system/jobseek-codex-daily-annotations.timer
install -o root -g root -m 0644 deploy/systemd/jobseek-codex-daily-error-review.service \
  /etc/systemd/system/jobseek-codex-daily-error-review.service
install -o root -g root -m 0644 deploy/systemd/jobseek-codex-daily-error-review.timer \
  /etc/systemd/system/jobseek-codex-daily-error-review.timer
install -o root -g codex-runner -m 0640 \
  deploy/systemd/jobseek-codex-governor.env.example \
  /etc/jobseek-codex/governor.env
systemctl daemon-reload
systemd-analyze verify \
  /etc/systemd/system/jobseek-codex-governor.service \
  /etc/systemd/system/jobseek-codex-governor.timer \
  /etc/systemd/system/jobseek-codex-daily-annotations.service \
  /etc/systemd/system/jobseek-codex-daily-annotations.timer \
  /etc/systemd/system/jobseek-codex-daily-error-review.service \
  /etc/systemd/system/jobseek-codex-daily-error-review.timer
```

Before enabling the timer, edit `/etc/jobseek-codex/governor.env` for the
host and keep `JOBSEEK_CODEX_DRY_RUN=true` until issue selection, host checks,
usage telemetry, ledger writes, and worktree creation have been verified. The
systemd service runs
`python3 /srv/jobseek-codex/repo/scripts/codex-company-resolver-governor.py`
under `flock -n /srv/jobseek-codex/state/codex-runner.lock`; daily services
use the same lock with a bounded wait. This keeps all local Codex routines at
one active process without firing missed daily jobs immediately after a
deployment.

For annotations, provision `/etc/jobseek-codex/labeller.env` with mode `0640`
and group `codex-runner`. Prefer a read-only local Postgres role that can
`SELECT` only the tables needed by the labeller (`job_posting`,
`descriptions`, `company`, and `job_board`). Do not put HuggingFace or Codex
tokens in this file; HuggingFace upload uses the runner user's local
HuggingFace cache.

After the dry-run and one manual live pass are clean, enable the timer:

```bash
systemctl enable --now jobseek-codex-governor.timer
systemctl enable --now jobseek-codex-daily-annotations.timer
systemctl enable --now jobseek-codex-daily-error-review.timer
```

Initial service limits:

```ini
[Service]
User=codex-runner
WorkingDirectory=/srv/jobseek-codex/repo
Type=oneshot
Nice=10
CPUQuota=200%
MemoryHigh=3G
MemoryMax=4G
TasksMax=1024
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/srv/jobseek-codex /home/codex-runner
UMask=0077
```

Apply host firewall or nftables owner rules for the `codex-runner` UID to
block private and local service ranges by default, especially
Redis/Postgres/Typesense private addresses and the Docker socket. Allow public
internet access needed for GitHub, OpenAI/ChatGPT, npm/pypi package
installation during setup, and HuggingFace trace upload if enabled. Do not
replace the local Hetzner timer with a GitHub Actions schedule.

### Phase 3 - governor decision loop

The systemd timer polls about every 1-1.5 minutes after the previous service
run exits. The governor still starts at most one resolver per service run and
uses ledger-backed pacing plus rolling five-hour caps to decide whether a wake
should actually start work.

Preflight gates:

- host health: load, available memory, disk space, and no active deploy or
  crawler incident
- no active resolver process in the local SQLite ledger, active `ws` claim, or
  open resolver PR for the same issue
- open `company-request` backlog exists
- weekly and five-hour Codex budget allow a run
- GitHub and Codex auth are valid enough to start

Usage inputs:

- Best-effort probe from `scripts/codex-usage-probe.py` for live scheduling.
- Local `codex exec --json` usage events recorded in `CODEX_EXEC_JSONL` and
  stored in the ledger for audit and later tuning.
- Conservative configured run budget when no live usage signal is available.

Scheduling policy:

- Default concurrency is `1`.
- The systemd lock is global across company resolver, daily annotations, and
  daily error review. A recurring resolver wake exits immediately if a daily
  routine is active; daily routines wait for an in-flight resolver up to six
  hours.
- Always keep a hard safety cap of at most
  `JOBSEEK_CODEX_MAX_RUNS_PER_5H` resolver issues per five-hour rolling window.
  The default `50` allows roughly 10 runs per hour when weekly usage remains
  above the fast threshold.
- Hard-block all new runs when either five-hour or weekly remaining usage is
  below `20%` by default (`JOBSEEK_CODEX_MIN_5H_REMAINING_PERCENT` and
  `JOBSEEK_CODEX_MIN_WEEKLY_REMAINING_PERCENT`).
- If a usage window is below the hard-block threshold, pause until that window
  reset, or for the fallback retry interval when the reset time is unknown.
- When weekly remaining usage is at least
  `JOBSEEK_CODEX_FAST_WEEKLY_REMAINING_PERCENT` (default `50`), use the fast
  five-hour budget, `JOBSEEK_CODEX_MAX_RUNS_PER_5H`, and the fast minimum
  start interval, `JOBSEEK_CODEX_FAST_MIN_START_INTERVAL_S` (default `360`,
  roughly 10 runs per hour).
- When weekly remaining usage is below that threshold, use the slower
  conservative five-hour budget, `JOBSEEK_CODEX_CONSERVATIVE_RUNS_PER_5H`, and
  the conservative minimum start interval,
  `JOBSEEK_CODEX_CONSERVATIVE_MIN_START_INTERVAL_S` (default `3600`).
- When usage telemetry is unavailable, run at the conservative floor instead
  of failing the scheduler permanently.
- Record every scheduler decision and usage window in the local SQLite
  `usage_snapshots` table so weekly and five-hour depletion speed can be
  reviewed before changing thresholds.

### Phase 4 - one-run execution

For each accepted issue:

1. Select the oldest eligible `company-request` issue, acquire a local SQLite
   lease, post a runner-owned `<!-- ws-claim -->` comment, then re-check claims
   and open PRs before launching Codex. If the runner loses the race, delete
   only its own claim and exit.
2. Fetch latest `origin/main`.
3. Create a fresh worktree under
   `/srv/jobseek-codex/worktrees/company-request-<issue>-<run-id>`.
4. Export `CODEX_EXEC_JSONL` under `/srv/jobseek-codex/traces/`.
5. Run local Codex CLI with one self-contained issue prompt. The prompt starts
   by telling Codex to run `uv run ws task --issue <N>` from `apps/crawler`,
   then follow only the instructions printed by `ws`.
6. Capture `codex exec --json` stdout to the JSONL trace path and stderr to a
   per-run log file.
7. Let `ws submit` create the PR; never push to `main`.
8. On `ws task complete`, upload the scoped trace only after the credential
   detector accepts the payload. A detected GitHub, OpenAI, HuggingFace, AWS,
   Google, Slack, bearer, JWT, private-key, URL-password, or sensitive
   assignment shape must fail the upload closed and leave the local trace for
   manual review.
9. Record PR URL, branch, usage summary, trace path, and final status in the
   ledger.
10. Remove the throwaway worktree after trace export and a successful PR, or
   retain it for bounded debugging on failure.

### Phase 5 - rollout

1. Dry-run mode: prove issue selection, host checks, usage probe, ledger
   writes, and owned-claim release without invoking Codex or creating a
   worktree.
2. Manual live mode: run one low-risk issue from the Hetzner shell with the
   timer disabled.
3. Timer canary: enable the timer with conservative floor scheduling and
   inspect two successful runs.
4. Adaptive mode: enable usage-aware scheduling and end-of-week catch-up.
5. Maintenance: review ledger summaries weekly and update thresholds when the
   observed Codex limits change.

## Routine Requirements

### Daily classifications

- Target exactly 10 accepted records for the current UTC date unless the
  manual invocation explicitly says otherwise.
- Check the remote HuggingFace dataset before doing work; a date with 10 rows
  is already complete.
- The Hetzner daily runner marks the run complete only after the remote
  `data/<YYYY-MM-DD>.jsonl` file has exactly 10 rows.
- Upload only accepted records after schema validation, QA validation, and
  targeted quality review.
- Preserve remote HuggingFace history and README counts when uploading.
- Verify `data/<YYYY-MM-DD>.jsonl` has exactly 10 rows after upload.
- Escalate labelling-quality issues that point to a prompt or model weakness;
  do not file routine data rejections as prompt/model issues by default.

### Daily error review

- Use an explicit 24-hour UTC log window.
- Use the root-collected redacted evidence bundle under
  `/srv/jobseek-codex/inputs/error-review/latest`; the Codex process must not
  access Docker, `/home/deploy`, or production env files directly.
- Collect host signals before log classification.
- Classify errors as `known`, `novel`, `regression`, `spike`, or `incident`.
- Append reruns to the same daily report instead of overwriting it.
- Deduplicate GitHub issues by service plus error class.
- Redact secrets from reports, traces, and GitHub content.
- The Hetzner daily runner marks the run complete only after the dated report
  exists, was updated during the run, and contains the required header/window.

### Company resolver

- Process at most one issue per recurring run.
- Respect the configured five-hour safety budget. Defaults are 50 runs per five
  hours in fast mode and 5 runs per five hours in conservative mode.
- Use `<!-- ws-claim -->` comments to claim work and skip active claims.
- The governor owns issue selection for recurring runs so it can pair GitHub
  claims with local SQLite leases and delete only its own claim on races.
- Run `ws task --issue <N>` from `apps/crawler`.
- Create PRs only; never push directly to `main`.
- Leave config-only additions on `add-company/<slug>` branches and code
  changes on `fix-crawler/<description>` branches.
- Manual recovery uses the same local Codex CLI path from a throwaway worktree
  and must still respect `ws` claims, the governor ledger, and trace capture.

## Maintenance Checks

Run these checks after changing automation docs, skills, or prompts:

```bash
git diff --check -- AGENTS.md docs .agents apps/crawler/src/labeller
rg -n "Claude-Code-orchestrat[e]d|Sonn[e]t subagents|O[p]us|ChatGPT subscription bill[e]d|postings/\\{\\{date\\}\\}/<id>\\.jso[n]" AGENTS.md docs .agents apps/crawler/src/labeller || true
cd apps/crawler && uv run labeller --help
```

For company resolver changes, also run the `ws` help path from
`apps/crawler` and inspect the generated task instructions for one issue
before enabling the Hetzner timer:

```bash
uv run ws help
uv run ws task --issue <N>
```

Use [17-codex-migration-verification-runbook.md](17-codex-migration-verification-runbook.md)
for pilot rollback criteria and trace-capture requirements.

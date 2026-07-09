# Codex Automation Deployment

This document is the repo-owned deployment and maintenance spec for recurring
Codex runs. Codex app automation records, local app TOML files, Hetzner
systemd units, governor state, and workflow dispatch settings are deployment
artifacts; do not treat them as source of truth and do not commit local Codex
app state.

## Automation Registry

| automation | cadence | execution | source of truth | model policy |
|---|---:|---|---|---|
| `jobseek-daily-classifications` | daily, 08:00 operator-local time | Codex app automation or local Codex CLI from this repo | [15-data-sampling-routine.md](15-data-sampling-routine.md), [`.agents/skills/jobseek-label-daily/SKILL.md`](../.agents/skills/jobseek-label-daily/SKILL.md) | strongest orchestrator; task-sized labeller subagents |
| `jobseek-daily-error-review` | daily, 09:00 operator-local time | Codex app automation or local Codex CLI from this repo | [14-error-review-routine.md](14-error-review-routine.md), [`.agents/skills/jobseek-error-review/SKILL.md`](../.agents/skills/jobseek-error-review/SKILL.md) | strongest model, high reasoning; no default subagents |
| `jobseek-company-request-resolver` | self-regulated, checked every 15-30 min | Hetzner crawler host, dedicated `codex-runner` user, local Codex CLI, isolated worktree per issue | [01-agent-workflow.md](01-agent-workflow.md), `apps/crawler/AGENTS.md`, `ws task --issue <N>` | strongest orchestrator; task-sized `ws` subagents |
| `manual-codex-company-resolver` | manual emergency only | [`.github/workflows/manual-codex-company-resolver.yml`](../.github/workflows/manual-codex-company-resolver.yml) | same `ws` contract as the recurring resolver | API-billed Codex fallback for missed runs or bounded backlog recovery |

The recurring company resolver must not be triggered by GitHub Actions. It
runs on the Hetzner crawler host through local Codex CLI auth so it can use the
subscription-backed Codex surface where possible. The manual GitHub Action
fallback intentionally has no schedule; use it only when a human explicitly
dispatches one issue or a bounded backlog and accepts API-key billing.

## Harness Invariants

These rules must hold whether the run is launched by the Codex desktop app,
Codex CLI, the Hetzner governor, a future Codex scheduler, or the manual
GitHub Action fallback.

- The automation prompt must be self-contained. It cannot rely on the
  conversation that created or updated the automation.
- The repo docs and skills above are the behavioral source of truth. Update
  them first, then update the deployed automation prompt or workflow prompt.
- Do not install or invoke Claude Code from Codex automations. Do not add
  scheduled API-billed Codex GitHub Actions for these routines.
- Do not add a GitHub Actions trigger for the recurring company resolver.
  GitHub Actions may remain as a manual emergency fallback only.
- Run the Hetzner company resolver under a dedicated local user with no sudo,
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

Use this process when creating or changing a Codex app automation, Hetzner
runner prompt, or manual fallback prompt:

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

## Hetzner Company Resolver Implementation Plan

The company resolver runs as a self-regulating local Codex job on the crawler
machine. The current crawler host has enough headroom for one low-priority
resolver at a time, but the runner must be isolated so it cannot consume
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
  repo/                 # bare or normal clone tracking origin/main
  worktrees/            # one throwaway worktree per issue/run
  traces/               # CODEX_EXEC_JSONL output, not committed
  state/ledger.sqlite   # governor decisions, usage, claims, run outcomes
  logs/                 # optional sanitized summaries; journald is primary
```

Install runtime tools in user-owned paths where possible: `git`, `gh`, Codex
CLI, Python, `uv`, Node/pnpm only if the `ws` flow or tests require them. Do
not grant write access to production crawler deployment directories.

### Phase 2 - network and process limits

Run the governor through `jobseek-codex-governor.service` and
`jobseek-codex-governor.timer`.

Committed deployment templates:

- [`../deploy/systemd/jobseek-codex-governor.service`](../deploy/systemd/jobseek-codex-governor.service)
  - `Type=oneshot`, low-priority CPU/IO scheduling, `CPUQuota=200%`,
  `MemoryHigh=3G`, `MemoryMax=4G`, `TasksMax=1024`, `ProtectSystem=strict`,
  and writable paths limited to `/srv/jobseek-codex` and
  `/home/codex-runner`.
- [`../deploy/systemd/jobseek-codex-governor.timer`](../deploy/systemd/jobseek-codex-governor.timer)
  - starts 15 minutes after boot, then 20 minutes after the previous service
  finishes with up to 10 minutes of jitter.
- [`../deploy/systemd/jobseek-codex-governor.env.example`](../deploy/systemd/jobseek-codex-governor.env.example)
  - non-secret governor defaults, including conservative budgets and usage
  thresholds.
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
  /srv/jobseek-codex/logs
install -d -o root -g codex-runner -m 0750 /etc/jobseek-codex

install -o root -g root -m 0644 deploy/systemd/jobseek-codex-governor.service \
  /etc/systemd/system/jobseek-codex-governor.service
install -o root -g root -m 0644 deploy/systemd/jobseek-codex-governor.timer \
  /etc/systemd/system/jobseek-codex-governor.timer
install -o root -g codex-runner -m 0640 \
  deploy/systemd/jobseek-codex-governor.env.example \
  /etc/jobseek-codex/governor.env
systemctl daemon-reload
systemd-analyze verify \
  /etc/systemd/system/jobseek-codex-governor.service \
  /etc/systemd/system/jobseek-codex-governor.timer
```

Before enabling the timer, edit `/etc/jobseek-codex/governor.env` for the
host and keep `JOBSEEK_CODEX_DRY_RUN=true` until issue selection, host checks,
usage telemetry, ledger writes, and worktree creation have been verified. The
systemd service runs
`python3 /srv/jobseek-codex/repo/scripts/codex-company-resolver-governor.py`
under `flock -n /srv/jobseek-codex/state/governor.lock`, so an overlapping
timer firing exits without starting a second resolver.

After the dry-run and one manual live pass are clean, enable the timer:

```bash
systemctl enable --now jobseek-codex-governor.timer
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

The governor runs every 15-30 minutes with randomized delay and exits quickly
when it should not start work.

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
- Always keep a hard safety cap of at most five resolver issues per five-hour
  rolling window unless deliberately raised in the deployment config.
- If five-hour remaining usage is low, pause until the window reset plus
  jitter.
- If weekly remaining usage is low, pause until the weekly reset, or for the
  fallback retry interval when the reset time is unknown.
- When the weekly window is near its end and a lot of usage remains, allow
  more runs so unused subscription capacity is converted into resolved
  backlog.
- When usage telemetry is unavailable, run at the conservative floor instead
  of failing the scheduler permanently.

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
8. Record PR URL, branch, usage summary, trace path, and final status in the
   ledger.
9. Remove the throwaway worktree after trace export and a successful PR, or
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
- Upload only accepted records after schema validation, QA validation, and
  targeted quality review.
- Preserve remote HuggingFace history and README counts when uploading.
- Verify `data/<YYYY-MM-DD>.jsonl` has exactly 10 rows after upload.
- Escalate labelling-quality issues that point to a prompt or model weakness;
  do not file routine data rejections as prompt/model issues by default.

### Daily error review

- Use an explicit 24-hour UTC log window.
- Collect host signals before log classification.
- Classify errors as `known`, `novel`, `regression`, `spike`, or `incident`.
- Append reruns to the same daily report instead of overwriting it.
- Deduplicate GitHub issues by service plus error class.
- Redact secrets from reports, traces, and GitHub content.

### Company resolver

- Process at most one issue per recurring run.
- Respect the five-issues-per-five-hours safety budget unless the Hetzner
  deployment config deliberately raises it.
- Use `<!-- ws-claim -->` comments to claim work and skip active claims.
- The governor owns issue selection for recurring runs so it can pair GitHub
  claims with local SQLite leases and delete only its own claim on races.
- Run `ws task --issue <N>` from `apps/crawler`.
- Create PRs only; never push directly to `main`.
- Leave config-only additions on `add-company/<slug>` branches and code
  changes on `fix-crawler/<description>` branches.
- Keep the manual Codex GitHub Action as an emergency-only fallback for missed
  runs or bounded backlog recovery.
- Keep the legacy resolver workflow manual-only, or remove it once the Codex
  resolver is stable.

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

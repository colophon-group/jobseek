# Codex Automation Deployment

This document is the repo-owned deployment and maintenance spec for recurring
Codex runs. Codex app automation records, local app TOML files, and workflow
dispatch settings are deployment artifacts; do not treat them as source of
truth and do not commit local Codex app state.

## Automation Registry

| automation | cadence | execution | source of truth | model policy |
|---|---:|---|---|---|
| `jobseek-daily-classifications` | daily, 08:00 operator-local time | Codex app automation or local Codex CLI from this repo | [15-data-sampling-routine.md](15-data-sampling-routine.md), [`.agents/skills/jobseek-label-daily/SKILL.md`](../.agents/skills/jobseek-label-daily/SKILL.md) | strongest orchestrator; task-sized labeller subagents |
| `jobseek-daily-error-review` | daily, 09:00 operator-local time | Codex app automation or local Codex CLI from this repo | [14-error-review-routine.md](14-error-review-routine.md), [`.agents/skills/jobseek-error-review/SKILL.md`](../.agents/skills/jobseek-error-review/SKILL.md) | strongest model, high reasoning; no default subagents |
| `jobseek-company-request-resolver` | hourly, off the hour | Codex app automation in an isolated worktree | [01-agent-workflow.md](01-agent-workflow.md), `apps/crawler/AGENTS.md`, `ws task --issue <N>` | strongest orchestrator; task-sized `ws` subagents |
| `manual-codex-company-resolver` | manual only | [`.github/workflows/manual-codex-company-resolver.yml`](../.github/workflows/manual-codex-company-resolver.yml) | same `ws` contract as the recurring resolver | API-billed Codex fallback for missed runs or bounded backlog recovery |

The manual GitHub Action fallback intentionally has no schedule. Use it only
when a human explicitly dispatches one issue or a bounded backlog. Scheduled
work should prefer Codex app automation or local Codex CLI so it stays on the
subscription-backed surface where possible.

## Harness Invariants

These rules must hold whether the run is launched by the Codex desktop app,
Codex CLI, a future Codex scheduler, or the manual GitHub Action fallback.

- The automation prompt must be self-contained. It cannot rely on the
  conversation that created or updated the automation.
- The repo docs and skills above are the behavioral source of truth. Update
  them first, then update the deployed automation prompt or workflow prompt.
- Do not install or invoke Claude Code from Codex automations. Do not add
  scheduled API-billed Codex GitHub Actions for these routines.
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

## Deployment Procedure

Use this process when creating or changing a Codex app automation:

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
- Respect the five-issues-per-five-hours budget.
- Use `<!-- ws-claim -->` comments to claim work and skip active claims.
- Reuse `.github/scripts/select-issue.sh` where possible.
- Run `ws task --issue <N>` from `apps/crawler`.
- Create PRs only; never push directly to `main`.
- Leave config-only additions on `add-company/<slug>` branches and code
  changes on `fix-crawler/<description>` branches.
- Keep the manual Codex GitHub Action as fallback for missed runs or bounded
  backlog recovery.
- Keep the legacy scheduled resolver disabled once the Codex resolver is
  stable by setting `ENABLE_COMPANY_RESOLVER=false`.

## Maintenance Checks

Run these checks after changing automation docs, skills, or prompts:

```bash
git diff --check -- AGENTS.md docs .agents apps/crawler/src/labeller
rg -n "Claude-Code-orchestrat[e]d|Sonn[e]t subagents|O[p]us|ChatGPT subscription bill[e]d|postings/\\{\\{date\\}\\}/<id>\\.jso[n]" AGENTS.md docs .agents apps/crawler/src/labeller || true
cd apps/crawler && uv run labeller --help
```

For company resolver changes, also run the `ws` help path from
`apps/crawler` and inspect the generated task instructions for one issue
before enabling the schedule:

```bash
uv run ws help
uv run ws task --issue <N>
```

Use [17-codex-migration-verification-runbook.md](17-codex-migration-verification-runbook.md)
for pilot rollback criteria and trace-capture requirements.

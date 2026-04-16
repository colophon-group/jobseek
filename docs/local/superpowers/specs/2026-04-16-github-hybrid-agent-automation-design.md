# GitHub Hybrid Agent Automation — Design Spec

**Status:** Awaiting user review of full spec. Chat approval already covered the high-level architecture; this document locks the repo-specific design before implementation.
**Date:** 2026-04-16
**Author:** Codex with @Milky-Mac
**Scope:** `jobseek` personal-use GitHub-first automation on a self-hosted runner attached to the user's Mac

---

## 0. Context

`jobseek` already uses GitHub as an agent control plane for one workflow: resolving company-request issues. The existing automation is a useful precedent because it already has:

- issue-driven workflows in `.github/workflows/`
- reusable shell helpers in `.github/scripts/`
- GitHub CLI usage for issue/PR mutation
- documented agent behavior in `docs/01-agent-workflow.md`
- existing auto-merge and review labeling policy in `docs/05-auto-merge.md`
- a repo-local `ai/` directory used as durable agent handoff state

That means the new system should extend these patterns instead of introducing a separate root-level orchestrator.

### Repo constraints that matter

- The current worktree is dirty. Implementation should happen in a fresh branch or worktree to avoid mixing with unrelated local-dev changes.
- This is for **personal use**, not a shared multi-user platform.
- The first version should prefer local authentication and local tools over cloud-only API orchestration.
- The system must remain visible and manageable from `github.com`.

### Goal

Add a minimal GitHub-hybrid automation loop that lets the user:

1. open or label an issue on GitHub
2. generate a written plan with Claude on the self-hosted runner
3. approve execution explicitly
4. run Codex against the approved plan
5. create or update a PR automatically
6. run a bounded review pass
7. inspect progress, artifacts, and final PR state entirely from GitHub

### Non-goals for version 1

- no fully autonomous merge-to-main flow
- no open-ended Claude/Codex conversation loops
- no multi-repo fleet management
- no queue scheduler or cron-based issue selection
- no replacement of the existing company-request resolver

---

## 1. Architecture

### Control plane

GitHub is the source of truth.

- `Issues` represent work requests.
- `Labels` represent workflow state.
- `PRs` represent implementation output.
- `Workflow runs` and comments represent machine progress.

The runner on the Mac is only an execution surface. If the runner is offline, work pauses, but state remains visible on GitHub.

### Role split

- `Claude` owns planning and optional high-signal review.
- `Codex` owns implementation and verification.
- `GitHub Actions` owns trigger routing and state transitions.

This avoids the common failure mode where both tools redesign the task repeatedly instead of finishing it.

### Shared state

The automation writes durable artifacts under `ai/runs/<issue-number>/`:

- `Goal.md`
- `Spec.md`
- `Plan.json`
- `Build.md`
- `Review.md`
- `Status.json`

These files are the handoff layer between workflow stages. They replace fragile reliance on chat history.

### Why `ai/runs/` instead of a new root folder

The repo already uses `ai/` as a durable handoff area. Extending that convention keeps the system legible and consistent with current repo practice.

---

## 2. File Layout

### New files

- `.github/workflows/agent-plan.yml`
- `.github/workflows/agent-build.yml`
- `.github/workflows/agent-review.yml`
- `.github/scripts/agent-lib.sh`
- `.github/scripts/agent-plan.sh`
- `.github/scripts/agent-build.sh`
- `.github/scripts/agent-review.sh`
- `.github/ISSUE_TEMPLATE/agent-task.yml`
- `docs/14-github-hybrid-agent-automation.md`

### New directories created by automation

- `ai/runs/<issue-number>/`

### Existing files expected to remain unchanged initially

- `.github/workflows/resolve-company-requests.yml`
- `.github/scripts/select-issue.sh`
- `docs/01-agent-workflow.md`
- `docs/05-auto-merge.md`

The new automation should coexist with the current company-request flow, not replace or entangle it.

---

## 3. Workflow Model

### Labels

Version 1 uses a strict label-driven state machine:

- `agent:plan`
- `agent:build-ready`
- `agent:build`
- `agent:blocked`
- `agent:done`

`agent:review` is still part of the system, but it is applied to the **PR**, not the issue. That keeps review approval attached to the actual diff being reviewed.

### `agent-plan` workflow

Trigger:
- issue labeled `agent:plan`

Behavior:
1. validate that the repo is private or explicitly allowlisted
2. create `ai/runs/<issue>/`
3. capture issue body and selected metadata into `Goal.md`
4. invoke Claude in planning mode
5. write `Spec.md`, `Plan.json`, and `Status.json`
6. comment a concise summary back to the issue
7. replace `agent:plan` with `agent:build-ready`

### `agent-build` workflow

Trigger:
- issue labeled `agent:build`

Preconditions:
- `Plan.json` exists for that issue
- the issue is already labeled `agent:build-ready`

Behavior:
1. derive or reuse branch `agent/<issue-number>-<slug>`
2. invoke Codex with a bounded execution brief built from `Plan.json`
3. run explicit verification commands from the plan
4. commit and push if files changed
5. create or update a PR
6. write `Build.md` and update `Status.json`
7. comment on the issue with the PR link and wait for the user to label the PR `agent:review`

### `agent-review` workflow

Trigger:
- PR labeled `agent:review`
- optional future extension: PR synchronize event when the PR already has `agent:review`

Behavior:
1. gather the approved plan, diff summary, and verification results
2. run a bounded review pass
3. comment findings on the PR
4. if clean, label the issue `agent:done`
5. if not clean, label the issue `agent:blocked` or return it to `agent:build`

### Retry policy

- one automatic retry for transient command failure
- no infinite replan/rebuild/review loop
- after the bounded retry budget, mark `agent:blocked`

This is a deliberate product choice. A system that fails closed is more useful than a system that burns time and tokens while drifting.

---

## 4. Approval and Safety Model

### Hard gates

- never push directly to `main`
- never auto-merge in v1
- fail closed on public repositories
- refuse build if `Plan.json` is missing
- refuse build if no explicit verification commands are present
- refuse to continue after repeated review failures

### Human approval surface

The user approves by label transitions visible on GitHub:

- `agent:plan` = create a plan
- `agent:build` = execute the approved plan
- PR label `agent:review` = run review on the PR

This keeps approval simple and browser-native.

### Security stance for self-hosted runner

Because the runner executes on the user's Mac, version 1 assumes:

- private repositories only
- no fork PR execution
- no unreviewed arbitrary shell from external contributors

The workflow docs should state this explicitly.

---

## 5. Runner and Tooling Assumptions

### Runner

Target runner selector:

```yaml
runs-on: [self-hosted, macOS, codex]
```

The user will install a repository-level or account-level self-hosted runner on the Mac and attach the `codex` label.

### Required local tools on the runner

- `git`
- `gh`
- `jq`
- `claude`
- `codex`

The scripts should fail early with useful error messages if any are missing.

### Authentication assumptions

- `gh` is already authenticated for the repo
- `claude` is locally authenticated on the Mac
- `codex` is locally authenticated on the Mac

Version 1 deliberately avoids adding API-key orchestration if local CLI auth already solves the problem.

---

## 6. Integration with Existing Repo Conventions

### Why shell scripts under `.github/scripts/`

The repo already uses shell scripts there for GitHub automation. Reusing that location reduces surprise and makes the new scripts easy to audit beside the existing ones.

### Why add a separate issue template

The current issue template is for company requests. General agent tasks need a distinct intake form so the automation can rely on structured fields like:

- goal
- target files or subsystem
- constraints
- verification commands
- optional reviewer notes

### Why a standalone operator doc

The existing docs describe company-request automation, not general-purpose personal engineering automation. A dedicated document keeps the new system teachable without polluting the company-request workflow docs.

---

## 7. Failure Modes and Intended Responses

### Runner offline

Effect:
- workflow queued or failed

Response:
- user sees failed check on GitHub
- no state corruption because issue and artifact files remain intact

### Claude planning failure

Effect:
- no `Plan.json`

Response:
- comment failure summary on the issue
- label `agent:blocked`

### Codex build failure

Effect:
- branch may exist without a clean PR update

Response:
- write failure details to `Build.md`
- comment on the issue
- label `agent:blocked`

### Review finds material problems

Effect:
- PR exists but is not ready

Response:
- comment findings on the PR
- move back to `agent:build` only if the failure is bounded and actionable
- otherwise mark `agent:blocked`

---

## 8. Verification Strategy

Implementation is successful when all of the following are true:

1. a labeled issue produces `ai/runs/<issue>/Goal.md`, `Spec.md`, `Plan.json`, and `Status.json`
2. a follow-up `agent:build` label creates or updates a PR on a dedicated branch
3. verification commands from the plan run and are surfaced in issue/PR comments
4. a review workflow comments on the PR using the saved plan and diff context
5. the system does not modify or interfere with the existing company-request resolver

---

## 9. Open Choices Deferred Intentionally

These are postponed until after the minimal loop works:

- slash-command control via `issue_comment`
- cron-based issue queue draining
- auto-merge for low-risk PRs
- API-key fallback when local CLIs are unavailable
- richer dashboards or tracing UI
- multi-repo shared automation package

These are useful, but they are not required to prove the core loop.

---

## 10. Design Self-Review

### Placeholder scan

No `TODO`, `TBD`, or hidden “figure it out later” requirements remain in the architecture. Deferred items are explicitly listed in Section 9.

### Internal consistency

The architecture consistently treats GitHub as the control plane, the Mac as the execution host, Claude as planner, and Codex as executor.

### Scope check

The scope is intentionally narrow enough for one implementation pass in this repo. It does not attempt cross-repo reuse or deep autonomous scheduling in version 1.

### Ambiguity check

The design explicitly chooses:

- `ai/runs/` for durable state
- `.github/scripts/` for executables
- label-driven control, not comment-driven control
- no auto-merge in v1

Those were the main ambiguous points during the discussion.

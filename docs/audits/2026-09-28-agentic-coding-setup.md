# Agentic coding setup audit — 28 September 2026

**Assessment:** Jobseek has substantial automation and test coverage, but several important guarantees exist only in instructions or are broken at the boundary between components. The most urgent problems are agent-writable code being executed as root and automatic merge eligibility being reused after the PR can change.

**Baseline:** latest `origin/main` fetched at the start of the audit, commit `6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca` (“Record deployed Go JSON-LD detail checkpoint”, #10138). Work was performed on `fix-crawler/codex-setup-audit` in an isolated managed worktree. Uncommitted changes in the primary checkout were excluded.

**Scope:** Codex instructions, custom agents and skills; the `ws` orchestration and state model; scheduled runner deployment and permissions; CI, review and merge controls; and developer verification ergonomics. This is a repository setup audit, not an exhaustive application vulnerability scan or a production host inspection.

## Priorities

P1 means address before relying further on the affected automation. P2 means a concrete reliability, security, or process gap to fix next. P3 means lower-priority documentation drift. “Reproduced” means a local deterministic probe; it does not imply an incident occurred in production.

| ID | Priority | Finding | Evidence |
|---|---|---|---|
| F01 | P1 | Privileged services execute agent-writable Python | Confirmed deployment/unit contract; host not inspected |
| F02 | P1 | Auto-merge does not bind eligibility to the commit being merged | Reproduced with stubbed Git/GitHub |
| F03 | P3 | Documentation still promises mandatory human reviews, contrary to current policy | Owner-confirmed policy, repository docs and live GitHub rules |
| F04 | P2 | CodeQL is described as required, but is outside the required checks | Repository configuration and live GitHub rules |
| F05 | P2 | Crawler instructions exceed Codex’s default discovery budget | Measured against official Codex documentation |
| F06 | P2 | Executable agent prompts are classified as documentation for CI | Reproduced path classification |
| F07 | P2 | Parallel config testers can overwrite or modify each other’s state | Two deterministic local reproductions |
| F08 | P2 | Agent guidance teaches an obsolete production deployment path | Direct conflict with the implemented deployment ADR |
| F09 | P2 | Every runner deployment can silently upgrade the Codex harness | Confirmed install and smoke-test commands |
| F10 | P2 | Resolver prompts blur the boundary between instructions and external data | Confirmed prompt construction; model exploitation not tested |

## Findings

### F01 — Privileged services execute agent-writable Python

The installer makes `/srv/jobseek-codex` runner-owned and clones the deployment repository as `codex-runner`. The Codex process runs with sandbox and approval bypass, while the systemd service permits writes throughout that root and the runner home. See [directory ownership](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/scripts/deploy-codex-runner-host.sh#L162), [checkout creation](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/scripts/deploy-codex-runner-host.sh#L237), [Codex defaults](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/workspace/codex_runner.py#L78), and [write scope](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/deploy/systemd/jobseek-codex-governor.service#L52).

That same checkout supplies root-executed programs:

- [Error-review pre/post hooks](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/deploy/systemd/jobseek-codex-daily-error-review.service#L30) execute `codex-routine-status.py` and `codex-error-review-bundle.py` with the privileged `+` prefix.
- [The Docker lifecycle watcher](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/deploy/systemd/jobseek-codex-docker-lifecycle.service#L14) runs as root from the checkout and has Docker access.
- [Collector imports](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/scripts/codex-error-review-bundle.py#L23) resolve a helper from the same writable script directory. Protecting only the entrypoint would leave that import exposed.

**Impact:** under the committed deployment arrangement, an agent command or compromised runner can modify code that a later privileged activation executes. A separate Unix account and removal from the Docker group do not establish isolation when root subsequently executes that account’s files. This is a concrete privilege-boundary defect; no attempt was made to exploit a host.

**Fix:** install the privileged collector, status helper, watcher, and their entire import closure into a root-owned release directory with root-owned ancestors. Invoke only those immutable paths from systemd. Separate the runner’s writable job output from deployment code and control state. A root-owned file inside a runner-writable parent is insufficient because the parent can allow replacement.

**Acceptance:** in a disposable Linux fixture, the runner UID cannot modify or replace any privileged executable/import/ancestor; replacing files in a job checkout has no effect on the root collector. Keep a negative test for this property. Codex also distinguishes technical sandbox restrictions from approval policy; unattended execution does not itself require disabling the sandbox. [Official guidance](https://learn.chatgpt.com/docs/agent-approvals-security).

### F02 — Auto-merge eligibility is not tied to the final PR head

[The merge script](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/scripts/maybe-auto-merge-pr.sh#L12) reads PR state and classifies the change once. It then fetches/rebases, waits for CI, and calls [`gh pr merge --rebase`](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/scripts/maybe-auto-merge-pr.sh#L153) without an expected head SHA or a fresh eligibility decision. This contradicts [the repository’s merge lease rule](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/AGENTS.md#L231).

**Reproduction:** the offline harness classifies head A as an eligible company configuration, simulates a move to head B before CI reports success, and records an unbound merge call on B. The original classifier runs only once. Git and GitHub were stubbed; no real PR was changed.

**Impact:** an initially eligible company branch can acquire code or other changes and still reach the original run’s merge attempt. GitHub continues to enforce its configured required checks and draft restrictions; the missing check is whether the *current content* still qualifies for autonomous merging. Another workflow’s later relabeling is not a reliable synchronization mechanism.

**Fix:** serialize automation per PR; re-read state, draft status, head/base OIDs, diff eligibility, holds and required checks immediately before merging. Reclassification must belong to the same head. Pass `--match-head-commit <verified-sha>`, which the [GitHub CLI supports](https://cli.github.com/manual/gh_pr_merge), and restart review on mismatch. Never reuse the old decision across retries.

**Acceptance:** change the head, base, review/hold state, or draft state between classification and merge; the attempt must fail closed or restart validation. Use the included mock as the starting regression case.

### F03 — Human-review instructions describe a retired policy

**Current policy, confirmed by the repository owner:** zero mandatory approving reviews is intentional. The GitHub setting is correct and is not an enforcement gap.

The [auto-merge documentation](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/docs/05-auto-merge.md#L83) nevertheless says code-change PRs always require human review. The [agent workflow](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/docs/01-agent-workflow.md#L121) repeats that requirement. The committed ruleset and live GitHub rules allow zero approving reviews, consistent with current policy. See [saved rule evidence](2026-09-28-agentic-coding-setup/github-main-rules.json).

**Impact:** agents can stop for an approval that the project does not require, or report the intended ruleset as a security defect. This is documentation drift, not a recommendation to introduce human-review enforcement.

**Fix:** remove the retired mandatory-review statements and describe the actual conditions for an authorized merge: applicable automated checks, current task authorization, and a fresh decision about the exact PR content. Keep the distinction between the company auto-merge bot's eligibility rules and broader authorized coding work explicit.

**Acceptance:** repo instructions consistently describe the zero-mandatory-review policy and do not invent an additional approval step. Preserve the intended GitHub review settings.

### F04 — CodeQL is outside the required merge checks

[The auto-merge documentation](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/docs/05-auto-merge.md#L93) says `Analyze (...)` CodeQL checks are required. Both [the committed rules](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/rulesets/main-strict-gate.json#L14) and the live effective rules require only `Required CI` and `Crawler Deploy Gate`. The [Required CI aggregator](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/workflows/ci.yml#L1812) does not incorporate the separate CodeQL workflow.

**Impact:** CodeQL can run and upload findings without being a prerequisite for merging. Listening to CodeQL’s `workflow_run` event in auto-merge is not equivalent to requiring success.

**Fix:** make the security policy explicit. If CodeQL is intended to be advisory, correct the documentation. If it is intended to prevent unsafe merges, add a required security gate with a defined finding policy. Handle data-only and prompt-only changes deliberately. Simply requiring today’s `Analyze (...)` names can strand documentation PRs because [the whole workflow is skipped](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/workflows/codeql.yml#L6) for those paths. Make the gate emit an explicit successful “not applicable” result where appropriate.

**Acceptance for an enforced gate:** a failed/missing security gate blocks a code PR; a data-only PR gets an explicit applicable/not-applicable result. Separately verify whether the desired policy blocks analysis failures, new findings above a threshold, or both.

### F05 — The crawler instruction chain does not fit Codex’s default budget

Measured sizes:

| Instruction file | Bytes | Lines |
|---|---:|---:|
| Root `AGENTS.md` | 15,844 | 239 |
| Crawler `AGENTS.md` | 45,792 | 907 |
| Combined | 61,636 | 1,146 |

Codex discovers instruction files from the repository root down to its starting directory, with a **32 KiB default combined budget**. [Official instruction-discovery documentation](https://developers.openai.com/codex/guides/agents-md).

Even before global guidance and separators, only 16,924 bytes remain for the crawler file: an approximate cutoff inside line 281. The crawler’s [decision rules](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/AGENTS.md#L518), [monitor development instructions](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/AGENTS.md#L529), and [code conventions](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/AGENTS.md#L900) occur later. The production prompt explicitly starts the workflow in `apps/crawler`, and the committed command builder supplies no larger budget.

**Impact:** default-configured Codex sessions can silently lack the instructions intended to govern their work. A root-started session also must not assume every nested instruction file was automatically preloaded. A developer manually opening a file is different from reliable startup discovery.

**Fix:** reduce each `AGENTS.md` to essential invariants, navigation and supported validation commands. Move operational reference material and provider-specific details into scoped runbooks/skills. Put high-impact rules first. Add a CI byte-budget check for each supported startup directory. Raising the cap can be a temporary compatibility measure, but does not solve irrelevant context load.

**Acceptance:** a fresh Codex invocation from both the repo root and `apps/crawler` identifies the intended rules without truncation. The measurement is confirmed; the effective production/user Codex configuration was not inspected, so actual session truncation remains conditional on its configured budget.

### F06 — Changes to executable prompts skip the relevant CI lane

[CI path filters](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/workflows/ci.yml#L166) exclude every `*.md` and all of `docs/`. [Manual PR classification](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/scripts/classify-pr-paths.sh#L23) follows the same convention. These exclusions include `AGENTS.md`, `SKILL.md`, shared labeller contracts and the `ws` Markdown templates that direct production behavior.

The offline classification probe returned `code=false` and `crawler_code=false` for six such instruction paths. Yet [the runner deployment workflow](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/workflows/deploy-codex-runner.yml#L6) explicitly deploys these paths on merge. The [crawler test job](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.github/workflows/ci.yml#L1331) is conditional on `code=true`.

**Impact:** behavioral changes to the agents can ship without the relevant crawler/prompt tests. Workflow-security and documentation-index checks still run; this is not a claim that every check is skipped. Agent TOML files already take the code path, making the treatment inconsistent across instruction formats.

**Fix:** add an explicit `agent_runtime` classification based on purpose, independent of file extension. Run skill/TOML validation, instruction-budget checks, template rendering, declared-command validation and selected `ws`/labeller contract tests for that lane. Use a small, versioned replay set for semantic prompt/model changes.

**Acceptance:** changing any production instruction source schedules its required contract checks. Ordinary prose documentation retains a cheap CI path.

### F07 — Parallel testers share state without a complete mutation contract

The orchestrator intentionally runs config testers concurrently. The [template](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/workspace/steps/parallel/config-tester.md#L220) tells them that named `--config` runs avoid conflicts, but the mutation commands do not provide the same isolation:

1. [`select_scraper`](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/workspace/commands/crawl.py#L1799) has no named-config selector. It writes to [`board.active_config`](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/workspace/commands/crawl.py#L1845). If B selects its monitor after A, A’s subsequent scraper selection modifies B’s config. The template also omits an explicit board on that selection command.
2. [`save_board`](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/workspace/state.py#L491) locks only while writing a whole in-memory board snapshot. Two callers can load the same state, edit independent configs, and save in sequence; the second snapshot erases the first caller’s new config. Atomic file replacement prevents partial files, not lost updates.

**Reproduction:** A’s scraper landed in B’s config with exit code 0. A second probe loaded two board snapshots before either save; after both saved, only B’s config remained. Both use the real repository code and temporary storage.

**Impact:** flaky comparison results, lost successful candidates and misleading feedback. More subagents increase exposure to this failure mode.

**Fix:** give every operation explicit workspace, board and config identity. Apply a config-scoped mutation under one read/modify/write lock, or return immutable candidate artifacts for the parent to commit. Long-running network tests should carry a config revision and reject stale result writes rather than hold a global lock.

**Acceptance:** a barrier-controlled two-worker test retains both independent candidates and their feedback. A scraper assignment never consults another worker’s active pointer.

### F08 — Agent instructions teach a deployment path that bypasses the release contract

The crawler [deployment instructions](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/AGENTS.md#L882) say to rsync a local source tree, build `crawler-slim:latest` on the host, restart a worker and run `crawler sync` directly. Similar instructions appear in the container-management section.

That conflicts with [ADR-006](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/docs/adr/006-crawler-deploy-quiescence-and-rollback.md#L25), which requires coordinated writer quiescence, exact runtime/data identities, serialized publication, migrations, verified release generations, readiness gates and rollback. In particular, running the displayed sync after restarting a worker does not follow the ADR’s writer-quiescence sequence.

**Impact:** an agent following a supposedly authoritative development guide can create unreviewed production drift or run a data transition outside the coordination protocol.

**Fix:** replace these snippets with links to the supported CI deployment and bounded recovery procedure. Make emergency operations explicit and record their required preconditions. Keep production mutation recipes in one maintained runbook.

**Acceptance:** an instruction consistency check rejects these old `rsync`/`:latest`/unguarded sync recipes from agent onboarding files. A deployment walkthrough reaches the exact-revision workflow and its rollback checks.

### F09 — Codex itself is unpinned and the deployment smoke does not test this setup

[`ensure_codex_cli`](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/scripts/deploy-codex-runner-host.sh#L193) installs `@openai/codex@latest` on each deployment. Its model smoke runs in `/tmp`, ignores user configuration and rules, uses low reasoning, and asks for “OK”. That establishes basic model/auth availability, but not compatibility with Jobseek’s skills, custom agents, startup instructions, permissions or workflow contracts.

**Impact:** the same repository revision can execute under different orchestration behavior after a routine redeployment. Changes to agent discovery, permissions or CLI output can arrive independently of the reviewed application change. Rolling the repo back does not restore the previous CLI.

**Fix:** commit the supported Codex CLI version and promote upgrades deliberately. Keep the cheap auth smoke, then add an isolated compatibility smoke using the real instruction sources and one harmless structured subagent task. Record CLI version, model/effort, instruction/config digest and repository SHA in run metadata. Add a small replay benchmark before changing model/effort policy.

**Acceptance:** reinstalling the same release yields the same CLI version, and rollback selects the prior supported version. A malformed custom-agent config or undiscoverable required skill fails the compatibility smoke.

The current standalone `.codex/agents/*.toml` layout is supported by [current Codex documentation](https://developers.openai.com/codex/subagents); lack of older-style agent registration in `.codex/config.toml` is **not** a finding.

### F10 — External issue text enters a supposedly authoritative instruction stream

The [resolver’s main prompt](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/workspace/codex_runner.py#L1527) calls `ws` output the runtime source of truth and relegates `AGENTS.md` to supporting guidance. The [pre-verification renderer](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/workspace/commands/task.py#L236) inserts the issue title/body verbatim into [the instructional template](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/workspace/steps/00-pre-verify.md#L12), without an explicit data-only boundary.

This also contradicts [the documented rule that tool output is evidence, not authority](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/docs/agents.md#L7). The labeller already demonstrates a better distinction: [its prompt labels external posting content as untrusted data](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/src/labeller/prompts/tasks/extract_all.md.j2#L30).

**Impact:** a request or discovered page can carry instructions mixed with the workflow guidance. This raises prompt-injection risk and creates ambiguity about which rules control a decision. It does not establish that Codex obeyed an injected instruction; no live attack was attempted.

**Fix:** retain repository/system policy as the authority. Separate trusted workflow steps from serialized external evidence, label provenance and trust explicitly, and state that issue/page text cannot authorize commands, credential access or workflow changes. Treat learned KB entries as reviewed data until promoted into instructions. Pair prompt clarity with technical permissions; wording alone is not an isolation mechanism.

**Acceptance:** include adversarial issue titles/bodies and page samples in a bounded replay suite. They must remain data and cannot redirect the agent outside the single assigned issue. Check permitted actions and durable outputs, not just whether a model says it ignored the injection.

## Development practices to improve after the immediate fixes

| Practice | Current evidence | Concrete improvement |
|---|---|---|
| Progressive instruction loading | A 907-line crawler `AGENTS.md` combines onboarding, provider internals and production operations | Use a short instruction entrypoint, scoped coding guidance, and explicit runbook links |
| Behavior-based safety tests | 592 selected tests pass while the board-state and merge probes still reproduce | Test interleavings and permission boundaries; retain structural checks as supplementary evidence |
| Reproducible verification | [Skill verification](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/.agents/skills/jobseek-label-daily/SKILL.md#L167) depends on a validator under one developer’s absolute home path | Vendor a small repository validator or declare a pinned portable tool; run it in CI and a clean checkout |
| Accurate type-check scope | [Pyright’s include list](https://github.com/colophon-group/jobseek/blob/6d1aa0c1a0690d2ed99475f3cbd5585a5b468eca/apps/crawler/pyrightconfig.json#L2) names six entrypoints, none in the Codex/`ws` harness | Expand incrementally over the runner and state APIs; describe the existing check as partial |
| Cohesive maintenance boundaries | Runner, lifecycle and crawl modules contain 3,743, 3,417 and 2,934 lines respectively | As these findings are fixed, extract cohesive policy/state/storage boundaries with explicit contracts; avoid an unrelated rewrite |
| Fast feedback by affected subsystem | Many web, crawler and shim jobs share the broad `code` flag | Introduce tested per-subsystem classifications plus a small cross-cutting gate; measure latency before optimizing further |

A missing file or additional framework is not automatically a problem. The practical goal is for a fresh Codex session to find the right rules, exercise the affected behavior cheaply, and remain unable to bypass publication controls.

## What is already working well

- The repository has a real `ws` state machine, deterministic labeller schemas/QA and task-specific contracts. The core structure is worth retaining.
- Codex custom agents define bounded jobs and explicit model/effort policies; shared labeller contracts avoid wholesale duplication between harnesses.
- The scheduled runner has a ledger, claims, retry/backoff, bounded process resources, environment filtering and trace handling. Dedicated users and redacted evidence bundles are sound directions once the privileged-code ownership defect is closed.
- CI has lint/type checks, database/search integration lanes, build/browser checks, dependency review and workflow-security analysis. GitHub Actions shown in the inspected workflows are pinned to commit SHAs.
- Frozen dependency installation in the audit worktree succeeded. The selected 498 Python tests and 94 repository-script tests passed.
- The deployment ADR and root merge rules describe useful invariants. Several findings are implementation/documentation drift from those existing decisions.

## Suggested remediation sequence

1. **Close privilege and publication gaps:** F01 and F02. Correct the stale review documentation while preserving zero mandatory reviews (F03), and resolve the CodeQL gate/documentation mismatch (F04). Keep each change independently reviewable and give it a negative acceptance test.
2. **Repair agent reliability:** F05–F07. Trim discovery inputs, add a required agent-runtime CI lane, and remove shared active-state assumptions from parallel tasks.
3. **Make upgrades and operation predictable:** F08–F10. Consolidate deployment guidance, pin/test the Codex harness, and separate external evidence from trusted instructions.
4. **Measure ongoing quality:** maintain a small representative replay set containing normal company setup, verified-empty board, multi-board overlap, conflicting evidence, state contention and malicious issue text. Track task outcome, retries, human intervention, elapsed time and token usage by harness/prompt revision.

These are recommendations only; this audit does not change production, rulesets, agent prompts or application behavior. Zero mandatory approving reviews is the intended policy and is explicitly preserved.

## Verification and limitations

[Offline reproduction script](2026-09-28-agentic-coding-setup/verify.py) and [captured output](2026-09-28-agentic-coding-setup/evidence.json) accompany this report. It uses temporary storage and stubs Git/GitHub for the merge and classification probes.

Commands run from the audit checkout:

```bash
cd apps/crawler
uv sync --frozen --group dev
uv run --frozen pytest tests/test_codex_runner.py \
  tests/test_codex_routine_runner.py tests/test_codex_runner_deploy_config.py \
  tests/test_workspace_state.py tests/test_ws_commands.py \
  tests/workspace/lib/test_select.py -q
# 498 passed in 22.38s

uv run --frozen python ../../docs/audits/2026-09-28-agentic-coding-setup/verify.py

cd ../..
node --test scripts/ci-workflow.test.mjs scripts/docs-index.test.mjs
# 94 passed
```

Read-only GitHub queries examined effective `main` rules and legacy branch protection; responses are saved in [github-main-rules.json](2026-09-28-agentic-coding-setup/github-main-rules.json). Official Codex instruction, subagent and permissions documentation and the GitHub CLI merge reference were consulted on the audit date.

No production SSH session, privileged execution, real merge, external publication, or live model evaluation was performed. Installed Hetzner file ownership, effective Codex configuration, token scopes and runtime host overrides remain unverified. The complete application test suite, frontend build, and production service-backed tests were outside this targeted audit. Findings distinguish locally reproduced behavior, static deployment contracts and live GitHub configuration accordingly.

# Codex Migration Verification Runbook

Use this runbook for Codex migration pilots before declaring a surface ready.
Run each pilot in an isolated worktree and keep a JSONL trace when the surface
is noninteractive.

## Trace Capture

Preferred noninteractive shape:

```bash
mkdir -p data/codex-pilots
export CODEX_EXEC_JSONL="data/codex-pilots/<surface>-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
codex exec --json "<pilot prompt>" | tee "$CODEX_EXEC_JSONL"
```

`apps/crawler/src/workspace/trace.py` looks for `CODEX_EXEC_JSONL` and related
`CODEX_*_JSONL`/`CODEX_TRACE_PATH` variables when exporting `ws` agent traces.
If no Codex JSONL file is available, trace export falls back to the internal
`ws` action log so completion remains best-effort.

For the recurring company resolver, use the Hetzner-hosted local Codex runner
documented in [18-codex-automation-deployment.md](18-codex-automation-deployment.md).
It should set `CODEX_EXEC_JSONL` for every accepted run and store the resulting
trace outside the repo. Do not trigger recurring company resolver work from
GitHub Actions.

For emergency resolver recovery, use the same local Codex CLI path from a
throwaway worktree and set `CODEX_EXEC_JSONL` explicitly. The retired GitHub
Actions fallback used OpenAI API-key billing and must not be described as
subscription-backed or reintroduced for the Hetzner-owned routines.

## Common Preflight

1. Confirm the worktree is clean enough for the pilot: `git status --short`.
2. Confirm required secrets are present only in the local environment or CI
   secret store, never in prompts or artifacts.
3. Set an explicit cost cap for LLM-backed pilots and record the cap in the
   trace.
4. Prefer repo skills from `.agents/skills` when present. If a skill is
   unavailable, paste the linked runbook as the prompt and record the fallback.

## Error Review Pilot

1. Run the Codex error-review skill or a `codex exec --json` prompt that follows
   `docs/14-error-review-routine.md`.
2. Verify it reads the last 24h of crawler errors, groups repeated failures
   against prior reviews, and drafts GitHub issues only for novel, regressing,
   or spiking errors.
3. Confirm repository code does not call Anthropic services directly.
4. Check that generated reports use the existing dated report layout and labels:
   `daily-error-review` plus one `error-review:*` severity.

Rollback criterion: revert to the Claude-compatible slash command if Codex
misses known recent errors, opens duplicate issues, cannot reach the Hetzner
logs, or emits an incomplete trace.

## Labeller Pilot

1. Run a small sample first, for example `uv run labeller sample --date today
   --count 2 --out <path>` from `apps/crawler/`.
2. For each sampled posting, run `prepare-pre-llm`, render and invoke the
   normalizer, run `prepare-post-llm`, then run `render-task`, subagent
   labelling, `validate`, `merge`, and final `validate --kind qa` exactly as
   described in `docs/15-data-sampling-routine.md`.
3. Keep task prompts and schemas provider-neutral. Agent-specific files may
   define invocation mechanics, but Jinja templates and validators remain the
   source of truth.
4. Upload only accepted records after validating the merged posting schema and
   QA gatekeeper output.

Rollback criterion: revert to the Claude-compatible route if Codex duplicates
prompt text across orchestrator/subagent contexts, skips validation retries,
uploads rejected records, or produces inconsistent block IDs.

## Enrichment OpenAI Smoke

OpenAI is the preferred provider for migration smokes; Anthropic and Gemini
remain supported. Do not change runtime defaults for the smoke: an empty
`ENRICH_PROVIDER` still means disabled.

1. Confirm the pilot branch has added an enrichment entry point before running
   API work. Current `main` does not register `uv run enricher`; use the exact
   replacement command added by the implementation branch.
2. Use a tiny batch and daily cap:

   ```bash
   ENRICH_PROVIDER=openai \
   ENRICH_MODEL=<openai-batch-capable-model> \
   ENRICH_API_KEY=$OPENAI_API_KEY \
   ENRICH_BATCH_SIZE=2 \
   ENRICH_MIN_BATCH_SIZE=1 \
   ENRICH_DAILY_SPEND_CAP_USD=1 \
   <enrichment-command> --dry-run --limit 2
   ```

3. After dry-run output is sane, run one live batch and one collect pass.
4. Verify persisted `enrich_batch.provider`, `model`, estimated cost, status,
   and `job_posting.enrichment.v`.
5. Compare at least two parsed results against the source posting HTML.

Rollback criterion: disable by clearing `ENRICH_PROVIDER` if cost estimation is
missing, schema validation fails, result parsing is provider-shaped outside the
provider adapter, or persisted fields drift from the provider-neutral schema.

## `ws` Resolver Manual Fallback

1. Run `ws task --issue <N>` in local or throwaway mode for one low-risk
   company-request issue.
2. If automation fails, use `codex exec --json` with the same AGENTS
   instructions, set `CODEX_EXEC_JSONL` as shown above, and explicitly run
   `ws resume` before continuing.
3. Verify duplicate search, monitor count comparison, scraper quality feedback,
   trace export, and `ws submit` behavior.

Rollback criterion: pause automation if Codex skips the verification loop,
submits without `ws feedback`, opens multiple active PRs for one issue, or
cannot resume from workspace state.

## Cost Guardrails

- Record model, provider, item count, and cap before any LLM-backed run.
- Prefer dry-run modes and tiny samples until the surface has two clean pilots.
- Stop the run if estimated cost is absent, exceeds the cap, or is computed in
  provider-specific code that bypasses the shared estimator.
- Never put API keys in Codex prompts, JSONL traces, GitHub comments, or PR
  bodies.
- For subscription-backed local Codex runs, record usage from `codex exec
  --json` events in the Hetzner governor ledger.
- The unofficial ChatGPT usage endpoint probe may be used as best-effort
  scheduling telemetry, but rollout must remain safe when it fails or changes
  shape. Fall back to local usage accounting and conservative run budgets.

## Stale Wording Scan

Run the stale-wording scan from the migration checklist before and after docs
changes. It should look for Claude-only orchestration wording, retired resolver
workflow names, direct-provider API phrasing, and claims that GitHub Actions
Codex runs are paid through a ChatGPT subscription.

The recurring company resolver and daily routines run through the Hetzner
local Codex runner. Docs, AGENTS files, and workflows should not reference
GitHub Actions fallback paths for those surfaces.

## Prompt-Duplication Checks

1. Search for duplicated task instructions between orchestrator prompts,
   subagent prompts, and Jinja task templates.
2. Confirm orchestrators pass rendered input/output paths rather than embedding
   full subagent contracts.
3. Keep validators and JSON Schemas as the source of truth for output shape.

Suggested scan:

```bash
rg -n "INPUT:|OUTPUT:|Previous attempt failed|qa_verdict|block_ids" .agents .claude apps/crawler/src/labeller docs || true
```

Rollback criterion: block the pilot if the same extraction contract appears in
multiple agent layers and produces conflicting instructions.

## Provider Boundary Checks

Provider-specific enrichment code should stay limited to:

- batch submission
- status polling
- result download and provider response parsing

Prompts, JSON Schema, validation, taxonomy resolution, cost estimation, and
persistence must remain provider-neutral.

Suggested scan:

```bash
rg -n "ENRICH_PROVIDER|anthropic|openai" docs apps/crawler/AGENTS.md AGENTS.md apps/crawler/src/core/enrich || true
```

Rollback criterion: stop the migration if OpenAI-specific assumptions leak into
shared prompts, schema validation, taxonomy mapping, cost accounting, or DB
persistence.

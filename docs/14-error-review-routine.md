# Daily error review - Codex-first ops routine

Codex-first routine for reviewing the last 24 hours of crawler errors on the
Hetzner box. It classifies errors against prior reviews, writes a dated report
to `~/dev/claude/review-jobseek-errors/`, deduplicates against GitHub issues,
and files or updates issues only for classes that are novel, regressing,
spiking, or incidents.

Prior exemplars (follow their shape): #2622, #2621, #2470, #2431.

## Invocation

- **Preferred scheduled route:** Hetzner local Codex runner through
  `jobseek-codex-daily-error-review.timer`. A root `ExecStartPre` collector
  writes a redacted read-only evidence bundle for the unprivileged
  `codex-runner` account, so the Codex process does not need Docker,
  `/home/deploy`, or production env access. Deployment settings and
  maintenance checks live in
  [18-codex-automation-deployment.md](18-codex-automation-deployment.md).
- **Preferred manual route:** local Codex CLI from the repo root, asking it to
  use the `jobseek-error-review` skill.
- **Manual traceable pilot:** run `codex exec --json` with the skill/runbook as
  the prompt and save the JSONL trace for agent trace collection checks.
- **Avoid:** GitHub Actions for this routine. Keep execution
  subscription-backed through the Hetzner runner or local Codex CLI where
  possible.
- **Claude fallback:** `/jobseek-error-review` remains available through the
  legacy Claude Code slash command for compatibility.

## Runbook Source

Primary source of truth:
[`.agents/skills/jobseek-error-review/SKILL.md`](../.agents/skills/jobseek-error-review/SKILL.md).

The skill preserves the existing behavior from the legacy slash command:
read-only host inspection, prior-report memory, GitHub issue dedupe, evidence
collection, redaction, and filing only for `novel`, `regression`, `spike`, or
`incident` classes.

Compatibility fallback:
[`.claude/commands/jobseek-error-review.md`](../.claude/commands/jobseek-error-review.md).
Keep it behaviorally aligned with the Codex skill when it is edited, but do
not treat Claude as the primary implementation path.

Do not spawn subagents by default. They are useful only for large independent
evidence sets and consume additional tokens; the main agent remains
responsible for classification, dedupe, redaction, and GitHub writes.

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

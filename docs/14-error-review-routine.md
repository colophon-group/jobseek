# Daily error review — scheduled Claude Code routine

A scheduled Claude Code agent that reviews the last 24h of crawler errors on
the Hetzner box, classifies them against prior reviews, writes a dated report
to `~/dev/claude/review-jobseek-errors/`, and files GitHub issues (label
`daily-error-review` + one `error-review:*` severity) for anything novel,
regressing, or spiking.

Prior exemplars (follow their shape): #2622, #2621, #2470, #2431.

## Invocation

- **Manual:** `/jobseek-error-review`
- **Scheduled (Claude Code desktop app):** point the schedule at the
  prompt `/jobseek-error-review`. Editing the slash command file
  updates the next run automatically — no need to re-set up the
  schedule.

## Prompt

The prompt is the slash command body at
[`.claude/commands/jobseek-error-review.md`](../.claude/commands/jobseek-error-review.md).
That file is the source of truth — edits propagate to the next run.

## Sibling routines

- [15 — Daily labelled-postings routine](15-data-sampling-routine.md) — same
  scheduled-routine shape, data-collection surface instead of error surface.

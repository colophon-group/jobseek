@AGENTS.md

## Subagent Model Selection

When dispatching subagents via the Agent tool, pick the model based on task scope:

- **haiku** — implementer tasks touching ≤2 files with a complete spec (mechanical, low judgment)
- **sonnet** — implementer tasks spanning multiple files, integration work, or spec compliance reviewers
- **opus** — code quality reviewers, architecture/design tasks, or any task requiring broad codebase understanding

If a haiku subagent returns BLOCKED or NEEDS_CONTEXT, re-dispatch with sonnet. If sonnet is stuck, escalate to opus.

## Execution Strategy by Phase Size

- **≤6 tasks**: use inline execution (`superpowers:executing-plans`) in the same session. Skip per-task reviews — do one final code review at the end.
- **>6 tasks**: use subagent-driven development (`superpowers:subagent-driven-development`) with per-task reviews.

The goal is to avoid spawning subagents for small phases where context reuse saves more than isolation gains.

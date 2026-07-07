# ADR-003: CSV Configuration Source of Truth

Status: implemented

Date: 2026-07-07

## Context

Company and board configuration is stored in
`apps/crawler/data/companies.csv` and `apps/crawler/data/boards.csv`. The `ws`
tool stages company onboarding work and submit flows create normal Git PRs
that change those CSV files. Deploy-time sync then derives local Postgres,
Supabase display subsets, Redis schedules, and Typesense taxonomy/company docs
from those committed files.

Murmur pipeline definitions and MCP access are being introduced as an
orchestration surface, but current transition docs explicitly keep `ws`, CSVs,
prompts, schema validation, and persistence rules outside provider-specific MCP
code.

## Decision

CSV files remain the source of truth for company and board configuration until
a future migration explicitly supersedes this ADR.

Runtime databases contain derived configuration state. They are not the
authoritative editing surface for company onboarding or board reconfiguration.
Murmur and workspace YAML may guide agents through the workflow, but accepted
changes must still land as reviewed CSV diffs.

A future DB-as-source-of-truth migration must add its own ADR before changing
this contract. That ADR must define ownership, audit history, delete/disable
semantics, rollback, sync cutover, and how agents review changes without losing
the current Git diff workflow.

## Consequences

- PRs that change companies or boards should include the CSV diff.
- Local Postgres and Supabase config rows can be regenerated from CSV sync.
- Ad hoc DB edits to company/board configuration are temporary operational
  repairs unless they are backported to CSV.
- Murmur MCP tooling must not silently create a second source of truth.
- Reviewers should reject changes that bypass CSV without an explicit migration
  plan.

## References

- [Data schema](../02-data-schema.md)
- [Agent workflow](../01-agent-workflow.md)
- [Murmur Codex MCP transition](../16-murmur-codex-mcp-transition.md)
- [`apps/crawler/src/sync.py`](../../apps/crawler/src/sync.py)
- [`apps/crawler/src/workspace`](../../apps/crawler/src/workspace)

# System Overview

## Architecture

Jobseek monitors company career pages for new job postings. The system is built around three ideas:

1. **Coding agents** add companies by creating PRs with CSV config changes
2. **CSV files** are the source of truth for what to monitor
3. **Redis-orchestrated workers** process boards and scrape jobs on Hetzner,
   exporting searchable results to Typesense through native Go processing

## System Flow

```
User request
  -> GitHub Issue (company-request label)
  -> Coding agent picks issue, creates PR
  -> PR adds rows to data/companies.csv + data/boards.csv
  -> PR merges (auto or human review)
  -> crawler sync: CSVs -> Local Postgres -> Redis queues + Typesense
  -> Workers claim from Redis, monitor boards, scrape jobs
  -> Results written to Local Postgres
  -> Go exporter CDC: Local Postgres -> Typesense
  -> R2 Drain: descriptions -> Cloudflare R2
  -> Job postings served to users via Typesense; web-owned data stays in Postgres
```

## Component Map

- `apps/web/`: Next.js frontend, web-owned data and product services.
- `apps/crawler/go/`: native production workers, queues, enrichment, export,
  description drain and maintenance.
- `apps/lightpanda/`: independently deployed production renderer.
- `apps/crawler/data/`: company/board registry and shared taxonomies.
- `apps/crawler/src/`: retained Python workspace, labeller, control tooling and
  native parity oracles; retired processing daemons and mirror writes are removed.
- `deploy/`, `.github/workflows/` and `scripts/`: protected deployment and
  operator workflows.

See the [crawler reference](reference/crawler.md) and
[production architecture](03-crawler-architecture.md) for maintained interfaces.

## Two Pipelines

### 1. Company Onboarding (agent-driven)

A user submits a company name or URL. The web app creates a GitHub issue
labeled `company-request`. The Hetzner-hosted Codex runner is the recurring
automation path for picking issues, researching companies, determining the
best monitor and scraper types, test-crawling, and creating PRs with CSV
config changes. Humans and AGENTS.md-compatible tools can follow the same
`ws` workflow manually. The PR merges automatically for low-risk additions or
gets human review for large/complex boards.

### 2. Job Monitoring (crawler-driven)

The crawler runs continuously on Hetzner. `sync.py` commits CSV configs to
local Postgres, then publishes Redis queues and Typesense registry documents.
Workers claim tasks from Redis tiered ready queues, run monitors to discover
listings, then scrape individual jobs when needed. Results are written to local
Postgres. An exporter CDC process publishes changed rows to Typesense and can
temporarily maintain a legacy relational mirror. An R2 drain uploads job
descriptions to Cloudflare R2.

## Key Design Decisions

- **CSV as source of truth**: Git history provides audit trail, diffs are reviewable, agents can edit files directly. The DB is derived state -- rebuilt from CSVs on each deploy.
- **Separated monitor + scraper**: A monitor discovers *which* jobs exist (URLs or full data). A scraper extracts *details* from individual pages. API monitors (Greenhouse, Lever) return full data and skip the scraper step entirely.
- **Local Postgres + Redis**: Workers read/write local Postgres (~0.1ms latency). Redis tiered queues handle work distribution with Lua scripts for atomic operations. Typesense receives denormalized CDC documents; the relational mirror is transitional.
- **Agent-driven onboarding**: No custom AI resolver code needed. Standard AGENTS.md-compatible coding agents (Codex, Claude Code, Copilot, Cursor, etc.) follow AGENTS.md instructions to add companies. The instructions are the interface.

## Related Documents

For the full documentation index, including routines, runbooks, historical
plans, and ADRs, start with [docs/README.md](./README.md).

- [01 -- Agent Workflow](./01-agent-workflow.md): How agents resolve company requests
- [02 -- Data Schema](./02-data-schema.md): CSV schemas, DB sync, config format
- [03 -- Crawler Architecture](./03-crawler-architecture.md): Redis pipeline, workers, exporter
- [04 -- Monitors and Scrapers](./04-monitors-and-scrapers.md): Types, configs, examples
- [05 -- Auto-Merge](./05-auto-merge.md): PR merge rules
- [07 -- System Design](./07-system-design.md): Infrastructure, subsystem details
- [08 -- Job Data Fields](./08-job-data-fields.md): Field reference and mappings
- [09 -- Enrichment](./09-enrichment.md): LLM-based enrichment pipeline
- [16 -- Murmur Codex MCP Transition](./16-murmur-codex-mcp-transition.md): Codex-first Murmur MCP plan
- [17 -- Codex Migration Verification Runbook](./17-codex-migration-verification-runbook.md): Pilot checklist and rollback criteria
- [18 -- Hetzner Codex Runner Deployment](./18-codex-automation-deployment.md): Production runner inventory, deployment, maintenance, and recovery boundary

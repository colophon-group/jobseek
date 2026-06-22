# System Overview

## Architecture

Jobseek monitors company career pages for new job postings. The system is built around three ideas:

1. **Coding agents** add companies by creating PRs with CSV config changes
2. **CSV files** are the source of truth for what to monitor
3. **Redis-orchestrated workers** process boards and scrape jobs on Hetzner, exporting results to Supabase for the web app

## System Flow

```
User request
  -> GitHub Issue (company-request label)
  -> Coding agent picks issue, creates PR
  -> PR adds rows to data/companies.csv + data/boards.csv
  -> PR merges (auto or human review)
  -> crawler sync: CSVs -> Local Postgres + Supabase + Redis queues
  -> Workers claim from Redis, monitor boards, scrape jobs
  -> Results written to Local Postgres
  -> Exporter CDC: Local Postgres -> Supabase (batch COPY)
  -> R2 Drain: descriptions -> Cloudflare R2
  -> Job postings served to users via Supabase
```

## Component Map

```
/
├── AGENTS.md                    # Agent instructions (provider-agnostic)
├── CLAUDE.md                    # @AGENTS.md import for Claude-compatible agents
├── data/
│   ├── companies.csv            # Company registry (slug, name, website, logos)
│   └── boards.csv               # Board configs (monitor type + scraper type per board)
├── docs/                        # This documentation
├── apps/
│   ├── web/                     # Next.js frontend + Drizzle schema
│   └── crawler/                 # Python crawler (asyncpg, httpx, structlog, redis)
│       └── src/
│           ├── core/            # Pure business logic
│           │   ├── monitors/    # Monitor implementations (35+ types)
│           │   ├── scrapers/    # Scraper implementations
│           │   ├── monitor.py   # monitor_one() dispatcher
│           │   └── scrape.py    # scrape_one() dispatcher
│           ├── workers/
│           │   ├── pipeline.py  # Discovery coroutines, claim from Redis, dispatch
│           │   └── r2_drain.py  # Producer-consumer: descriptions -> R2
│           ├── processing/
│           │   ├── board.py     # Streaming monitor processing, timestamp gone detection
│           │   ├── scrape.py    # Single-job scraping, fallback chain
│           │   ├── cpu.py       # CPU-bound processing (salary, location, etc.)
│           │   └── r2_stage.py  # Stage descriptions for R2 upload
│           ├── queries/
│           │   ├── monitor.py   # SQL: DIFF_BATCH, MARK_GONE, record success/fail
│           │   ├── scrape.py    # SQL: UPDATE_JOB_CONTENT, RECORD_SCRAPE_*
│           │   └── lookups.py   # Cached lookup table loaders
│           ├── redis_queue.py   # Lua-backed claim/enqueue/reschedule
│           ├── lua/             # claim_work.lua, enqueue_task.lua, reschedule_task.lua
│           ├── exporter.py      # CDC: local Postgres -> Supabase
│           ├── sync.py          # CSV -> Local Postgres + Supabase + Redis
│           ├── bootstrap.py     # One-time: Supabase -> local Postgres copy
│           ├── cli.py           # Entry point: crawler run/run-browser/export/drain/sync/board
│           ├── config.py        # Settings
│           └── metrics.py       # Prometheus metrics
└── .github/workflows/
    ├── resolve-company-requests.yml  # Agent picks issues hourly
    ├── auto-merge-config.yml         # Auto-merge low-risk config PRs
    └── deploy-crawler-browser.yml    # Build + deploy to Hetzner
```

## Two Pipelines

### 1. Company Onboarding (agent-driven)

A user submits a company name or URL. The web app creates a GitHub issue labeled `company-request`. A coding agent (Codex preferred for new automation, Claude-compatible paths still supported, or a crowd-sourced user agent) picks the issue, researches the company, determines the best monitor and scraper types, test-crawls, and creates a PR adding rows to the CSV config files. The PR merges automatically for low-risk additions or gets human review for large/complex boards.

### 2. Job Monitoring (crawler-driven)

The crawler runs continuously on Hetzner. `sync.py` loads CSV configs into local Postgres, Supabase, and Redis queues. Workers claim tasks from Redis tiered ready queues, run monitors to discover listings, then scrape individual jobs when needed. Results are written to local Postgres. An exporter CDC process batch-copies changed rows to Supabase every 1-2 seconds. An R2 drain uploads job descriptions to Cloudflare R2.

## Key Design Decisions

- **CSV as source of truth**: Git history provides audit trail, diffs are reviewable, agents can edit files directly. The DB is derived state -- rebuilt from CSVs on each deploy.
- **Separated monitor + scraper**: A monitor discovers *which* jobs exist (URLs or full data). A scraper extracts *details* from individual pages. API monitors (Greenhouse, Lever) return full data and skip the scraper step entirely.
- **Local Postgres + Redis**: Workers read/write local Postgres (~0.1ms latency). Redis tiered queues handle work distribution with Lua scripts for atomic operations. Supabase receives write-only batch exports via CDC.
- **Agent-driven onboarding**: No custom AI resolver code needed. Standard AGENTS.md-compatible coding agents (Codex, Claude Code, Copilot, Cursor, etc.) follow AGENTS.md instructions to add companies. The instructions are the interface.

## Related Documents

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

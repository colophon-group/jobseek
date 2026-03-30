# AGENTS.md — Jobseek

Instructions for developer agents working on this repository.

## Project Overview

Jobseek monitors company career pages for new job postings. Companies are configured via CSV files in `apps/crawler/data/`. A Python crawler monitors boards and extracts job details. A Next.js frontend serves the data.

## Repository Structure

```
/
├── apps/
│   ├── web/                 # Next.js 15 frontend (TypeScript, Drizzle ORM, Lingui i18n)
│   └── crawler/             # Python crawler (asyncpg, httpx, structlog, redis)
│       ├── data/
│       │   ├── companies.csv    # Company registry (slug, name, website, logos)
│       │   ├── boards.csv      # Board configs (monitor + scraper per board)
│       │   └── images/          # Logo/icon staging area, uploaded to R2 by CI
│       └── src/
│           ├── core/        # Pure business logic (monitors + scrapers)
│           ├── workers/     # Worker pipeline (claim from Redis, dispatch)
│           ├── processing/  # Board/scrape processing, CPU work, R2 staging
│           ├── queries/     # SQL queries for local Postgres
│           ├── redis_queue.py # Lua-backed claim/enqueue/reschedule
│           ├── lua/         # Redis Lua scripts
│           ├── exporter.py  # CDC: local Postgres -> Supabase
│           ├── sync.py      # CSV -> DB + Redis sync
│           ├── cli.py       # Entry point (crawler run/export/drain/sync/board)
│           └── config.py    # Settings
├── docs/                    # Architecture documentation
└── .github/workflows/       # CI + agent automation
```

## Commands

Crawler (from `apps/crawler/` — see [apps/crawler/AGENTS.md](apps/crawler/AGENTS.md) for full reference):

```bash
uv sync                           # Install dependencies
uv run pytest tests/              # Run tests
uv run crawler run                # Run HTTP worker (claims from Redis simple queues)
uv run crawler run-browser        # Run browser worker (claims from Redis browser queues)
uv run crawler export             # Run CDC exporter (local Postgres -> Supabase)
uv run crawler drain              # Run R2 description uploader
uv run crawler sync               # Sync CSVs to local Postgres + Supabase + Redis
uv run crawler board <slug>       # Process single board (debug)
```

Web app (from `apps/web/`):

```bash
pnpm dev          # Dev server
pnpm build        # Build (compiles i18n catalogs first)
pnpm db:migrate   # Run Drizzle migrations
pnpm db:seed      # Seed test data
pnpm extract      # Extract i18n strings to .po
pnpm compile      # Compile .po to .js catalogs
```

## Crawler Setup Workflow (`ws` tool)

The `ws` CLI is an **agent utility** — it is run exclusively by Claude Code
agents, not by humans directly. It guides the agent through the company
setup workflow by rendering instructions, managing state, and enforcing
quality gates.

**Entry point:** `ws task --issue <N>` — fetches the issue, renders
pre-verification instructions, then (after `ws new`) renders the parallel
orchestrator which tells the agent to spawn subagents for independent work.

**Instruction sources** (modify these to change agent behavior):
- Orchestrator + subagent prompts: `apps/crawler/src/workspace/steps/parallel/`
- `ws help` reference docs: `apps/crawler/src/workspace/commands/help.py`
- Troubleshooting KB: `apps/crawler/src/workspace/kb/*.md`
- Workflow gates: `apps/crawler/src/workspace/workflow.yaml`

Developer guidance for agent reasoning style lives in [docs/agents.md](docs/agents.md).

## Git Workflow

- Branch naming: `add-company/<slug>` for company additions, `fix-crawler/<description>` for code changes
- Commit messages: imperative mood, concise (`Add Stripe`, `Fix sitemap parser timeout`)
- Never push directly to main — always create a PR

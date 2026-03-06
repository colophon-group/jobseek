# AGENTS.md — Jobseek

Instructions for developer agents working on this repository.

## Project Overview

Jobseek monitors company career pages for new job postings. Companies are configured via CSV files in `apps/crawler/data/`. A Python crawler monitors boards and extracts job details. A Next.js frontend serves the data.

## Repository Structure

```
/
├── apps/
│   ├── web/                 # Next.js 15 frontend (TypeScript, Drizzle ORM, Lingui i18n)
│   └── crawler/             # Python crawler (asyncpg, httpx, structlog)
│       ├── data/
│       │   ├── companies.csv    # Company registry (slug, name, website, logos)
│       │   ├── boards.csv      # Board configs (monitor + scraper per board)
│       │   └── images/          # Logo/icon staging area, uploaded to R2 by CI
│       └── src/
│           ├── core/        # Pure business logic (monitors + scrapers)
│           ├── batch.py     # Batch processor
│           ├── scheduler.py # Poll-loop scheduler
│           ├── sync.py      # CSV → DB sync
│           └── inspect.py   # CSV validation + diagnostics
├── docs/                    # Architecture documentation
└── .github/workflows/       # CI + agent automation
```

## Commands

Crawler (from `apps/crawler/` — see [apps/crawler/AGENTS.md](apps/crawler/AGENTS.md) for full reference):

```bash
uv sync                           # Install dependencies
uv run pytest tests/              # Run tests
uv run scheduler                  # Run crawler poll loop
uv run python -m src.sync         # Sync CSVs to database
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

## Git Workflow

- Branch naming: `add-company/<slug>` for company additions, `fix-crawler/<description>` for code changes
- Commit messages: imperative mood, concise (`Add Stripe`, `Fix sitemap parser timeout`)
- Never push directly to main — always create a PR

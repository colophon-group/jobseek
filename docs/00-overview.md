# System Overview

## Architecture

Jobseek monitors company career pages for new job postings. The system is built around three ideas:

1. **Coding agents** add companies by creating PRs with CSV config changes
2. **CSV files** are the source of truth for what to monitor
3. **Three-layer crawler** (scheduler / batch / single job) runs anywhere

## System Flow

```
User request
  → GitHub Issue (company-request label)
  → Coding agent picks issue, creates PR
  → PR adds rows to data/companies.csv + data/boards.csv
  → PR merges (auto or human review)
  → DB sync on deploy reads CSVs → upserts company + board rows
  → Monitor discovers job listings on each board
  → Scraper extracts details from individual pages (when needed)
  → Job postings stored in DB → served to users
```

## Component Map

```
/
├── AGENTS.md                    # Agent instructions (provider-agnostic)
├── CLAUDE.md                    # @AGENTS.md import for Claude Code
├── data/
│   ├── companies.csv            # Company registry (slug, name, website, logos)
│   └── boards.csv               # Board configs (monitor type + scraper type per board)
├── docs/                        # This documentation
├── apps/
│   ├── web/                     # Next.js frontend + Drizzle schema
│   └── crawler/                 # Python crawler (monitor + scrape pipeline)
│       └── src/
│           ├── core/            # Pure business logic
│           │   ├── monitors/    # Monitor implementations (greenhouse, lever, sitemap, discover)
│           │   ├── scrapers/    # Scraper implementations (jsonld, html, browser)
│           │   ├── monitor.py   # monitor_one() — single board check
│           │   └── scrape.py    # scrape_one() — single URL extraction
│           ├── batch.py         # Batch processor (claims + runs N jobs)
│           ├── scheduler.py     # Poll-loop scheduler
│           ├── sync.py          # CSV → DB sync
│           ├── validate.py      # CSV validation (CI + agents)
│           ├── db.py            # DB connection pool
│           ├── config.py        # Settings
│           └── shared/          # HTTP client, logging, slug utility
└── .github/workflows/
    ├── resolve-company-requests.yml  # Agent picks issues hourly
    └── auto-merge-config.yml         # Auto-merge low-risk config PRs
```

## Two Pipelines

### 1. Company Onboarding (agent-driven)

A user submits a company name or URL. The web app creates a GitHub issue labeled `company-request`. A coding agent (Claude Code via GitHub Actions, or a crowd-sourced user agent) picks the issue, researches the company, determines the best monitor and scraper types, test-crawls, and creates a PR adding rows to the CSV config files. The PR merges automatically for low-risk additions or gets human review for large/complex boards.

### 2. Job Monitoring (crawler-driven)

The crawler runs continuously. A scheduler triggers the batch processor, which claims boards due for checking from the DB. For each board, a single job runs the monitor to discover listings, then the scraper to extract details (if needed). Results are written to the DB. The scheduler, batch processor, and single jobs are separate layers so the crawler can run on Fly.io (poll loop), Apify (cron), GitHub Actions (one-shot), or locally (CLI).

## Key Design Decisions

- **CSV as source of truth**: Git history provides audit trail, diffs are reviewable, agents can edit files directly. The DB is derived state — rebuilt from CSVs on each deploy.
- **Separated monitor + scraper**: A monitor discovers *which* jobs exist (URLs or full data). A scraper extracts *details* from individual pages. API monitors (Greenhouse, Lever) return full data and skip the scraper step entirely.
- **Three-layer crawler**: The single-job layer is a pure function with no DB awareness. The batch layer handles concurrency and DB writes. The scheduler layer is environment-specific. This separation makes the core logic testable and portable.
- **Agent-driven onboarding**: No custom AI resolver code needed. Standard coding agents (Claude Code, Copilot, Codex, etc.) follow AGENTS.md instructions to add companies. The instructions are the interface.

## Related Documents

- [01 — Agent Workflow](./01-agent-workflow.md): How agents resolve company requests
- [02 — Data Schema](./02-data-schema.md): CSV schemas, DB sync, config format
- [03 — Crawler Architecture](./03-crawler-architecture.md): Three-layer design
- [04 — Monitors and Scrapers](./04-monitors-and-scrapers.md): Types, configs, examples
- [05 — Auto-Merge](./05-auto-merge.md): PR merge rules
- [06 — Migration](./06-migration.md): From current draft to new architecture

# AGENTS.md — Jobseek

Instructions for coding agents working on this repository.

## Project Overview

Jobseek monitors company career pages for new job postings. Companies are configured via CSV files in `data/`. A Python crawler monitors boards and extracts job details. A Next.js frontend serves the data.

## Repository Structure

```
/
├── data/
│   ├── companies.csv        # Company registry (slug, name, website, logos)
│   └── boards.csv           # Board configs (monitor + scraper per board)
├── apps/
│   ├── web/                 # Next.js 15 frontend (TypeScript, Drizzle ORM, Lingui i18n)
│   └── crawler/             # Python crawler (asyncpg, httpx, structlog)
│       └── src/
│           ├── core/        # Pure business logic (monitors + scrapers)
│           ├── batch.py     # Batch processor
│           ├── scheduler.py # Poll-loop scheduler
│           ├── sync.py      # CSV → DB sync
│           └── validate.py  # CSV validation
├── docs/                    # Architecture documentation
└── .github/workflows/       # CI + agent automation
```

## Commands

All crawler commands run from `apps/crawler/`:

```bash
# Validate CSV files
uv run python -m src.validate

# Auto-detect monitor type for a URL
uv run python -m src.validate --detect <url>

# Check if a job page has JSON-LD
uv run python -m src.validate --probe-jsonld <job-page-url>

# Test crawl a board
uv run python -m src.validate --test-monitor <company-slug> <board-url>

# Sync CSVs to database
uv run python -m src.sync
uv run python -m src.sync --dry-run

# Run the crawler (poll loop)
uv run scheduler

# Run one batch and exit
uv run scheduler --once

# Run tests
uv run pytest tests/

# Install dependencies
uv sync
```

Web app commands run from `apps/web/`:

```bash
pnpm dev          # Dev server
pnpm build        # Build (compiles i18n catalogs first)
pnpm db:migrate   # Run Drizzle migrations
pnpm db:seed      # Seed test data
pnpm extract      # Extract i18n strings to .po
pnpm compile      # Compile .po to .js catalogs
```

## How to Add a Company

This is the primary task agents perform. Follow these steps exactly.

### 0. Verify GitHub CLI Auth

Before starting, verify that the GitHub CLI is authenticated:

```bash
gh auth status
```

If not authenticated, ask the user to run `gh auth login` in the current console session before proceeding. Do not continue until `gh auth status` succeeds.

### 1. Claim the Issue

Before creating a PR, check for existing open PRs:

```bash
gh pr list --state open --search "Closes #<issue-number>"
```

- If an open PR exists and has recent activity (commits within 24h for config PRs, 72h for code PRs) → **stop processing entirely**. The issue is actively claimed. Do not create a competing PR or continue to subsequent steps.
- If an open PR exists but appears stale (no recent commits beyond the thresholds above) → leave a comment on the stale PR noting it may be abandoned, then **proceed** to create your own PR as normal.
- If no open PR exists → create a draft PR with `Closes #<issue-number>` in the body.
  Use branch name `add-company/<slug>`.

### 2. Research the Company

Find:
- **Official name**: The company's official/common name
- **Website**: Company homepage (e.g., `https://stripe.com`)
- **Logo URL**: Direct link to logo image file (SVG or PNG, not a page)
- **Icon URL**: Favicon or small icon (check `/favicon.ico`, `<link rel="icon">` in HTML, or Google Favicons: `https://www.google.com/s2/favicons?domain=example.com&sz=128`)
- **Career page URL(s)**: The job board URL(s)

### 3. Detect the Monitor Type

```bash
cd apps/crawler
uv run python -m src.validate --detect <board-url>
```

If auto-detection fails, identify manually:
- URL contains `greenhouse.io` or page references Greenhouse API → `greenhouse`
- URL contains `lever.co` or page references Lever API → `lever`
- Site has XML sitemap with job URLs → `sitemap`
- JS-rendered SPA, no sitemap → `discover`

### 4. Test Crawl and Verify Monitor

```bash
uv run python -m src.validate --test-monitor <slug> <board-url>
```

After the test crawl:
1. Check the career page for a displayed job count (e.g. "Showing 247 open positions")
2. Compare the crawled count against the website's count:
   - **Match (within ~10%)** → proceed to step 5
   - **Significant gap** → investigate and iterate:
     - Wrong monitor type? Try alternatives (e.g. sitemap → discover)
     - Sitemap missing job URLs? Try `discover` monitor instead
     - API returning partial results? Check pagination config
     - Custom domain hiding behind a different ATS? Re-run `--detect`
   - Re-run test crawl after each change until counts align
   - If a gap remains after trying all options, document the discrepancy with an explanation

### 5. Configure and Verify Scraper

API monitors (`greenhouse`, `lever`) return full data — skip to step 7.

For URL-only monitors (`sitemap`, `discover`):

1. **Probe JSON-LD** (try this first):
   ```bash
   uv run python -m src.validate --probe-jsonld <a-job-page-url>
   ```
   - JSON-LD found → `scraper_type: json-ld` (no config needed)
   - No JSON-LD → inspect page HTML for CSS selectors → `scraper_type: html`
   - Page needs JS to render → `scraper_type: browser` (CSS selectors + wait strategy)

2. **Verify extraction** on 2–3 sample job URLs:
   - Does the title extract correctly?
   - Does the location extract correctly?
   - Does the description extract correctly?

3. **Iterate** if extraction is wrong or incomplete:
   - Revise CSS selectors
   - Try a different scraper type (e.g. `json-ld` → `html`, `html` → `browser`)
   - Repeat until extraction works on all samples

### 6. Escalate to Code Changes (when needed)

If no existing monitor/scraper type can handle the site after exhausting config options:

1. **Close the draft PR** created in step 1 (the `add-company/<slug>` branch)
2. Document what was tried and why it failed
3. Create a new PR on a `fix-crawler/<description>` branch
4. In the new PR body, reference the closed draft PR (e.g. "Supersedes #12")
5. Ensure the new PR closes the original issue (`Closes #<issue-number>`)
6. Apply label: `review-code` (always requires human review)
7. Include both the code change AND the CSV config in the same PR

### 7. Add CSV Rows

Add to `data/companies.csv`:
```csv
<slug>,<name>,<website>,<logo_url>,<icon_url>
```

Add to `data/boards.csv`:
```csv
<slug>,<board_url>,<monitor_type>,<monitor_config>,<scraper_type>,<scraper_config>
```

### 8. Validate

```bash
uv run python -m src.validate
```

### 9. Create the PR

Mark PR as ready. Include in body:
- Monitor type, scraper type
- Estimated job count and crawl time
- Apply label: `auto-merge` (<500 jobs, config-only) or `review-size`/`review-load` (larger)

## CSV Schemas

### data/companies.csv

```
slug,name,website,logo_url,icon_url
```

- `slug`: lowercase alphanumeric + hyphens, unique, no leading/trailing hyphens
- `name`: display name
- `website`: homepage URL with scheme
- `logo_url`: direct image URL (optional)
- `icon_url`: direct icon URL (optional)

### data/boards.csv

```
company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config
```

- `company_slug`: must exist in companies.csv
- `board_url`: unique career page URL
- `monitor_type`: `greenhouse` | `lever` | `sitemap` | `discover`
- `monitor_config`: JSON string (use `""` for inner quotes)
- `scraper_type`: `greenhouse_api` | `lever_api` | `json-ld` | `html` | `browser` (empty for API monitors)
- `scraper_config`: JSON string (empty for json-ld, greenhouse_api, lever_api)

## Code Style (Python — apps/crawler)

- Python 3.12+, async/await throughout
- `asyncpg` for Postgres (no ORM), `httpx` for HTTP, `structlog` for logging
- Type hints on all function signatures
- Dataclasses for data structures (not Pydantic, except for settings)
- `from __future__ import annotations` at top of every module
- Imports: stdlib → third-party → local, separated by blank lines
- No wildcard imports

## Git Workflow

- Branch naming: `add-company/<slug>` for company additions, `fix-crawler/<description>` for code changes
- Commit messages: imperative mood, concise (`Add Stripe`, `Fix sitemap parser timeout`)
- PR body: include issue reference (`Closes #N`), monitor/scraper types, estimates
- Never push directly to main — always create a PR

## Boundaries

### Always (do without asking)
- Validate CSVs before committing
- Test crawl before submitting PR
- Verify crawled job count against the website's displayed total
- Test scraper extraction on 2–3 sample URLs before committing
- Include job count estimates in PR body
- Follow the CSV schemas exactly

### Ask (check with maintainer first)
- Changing database schema
- Modifying CI/CD workflows
- Adding new dependencies

### Escalate (propose code changes via PR with `review-code` label)
- Existing monitor types can't discover all jobs on a board
- Existing scraper types can't extract data from job pages
- Adding a new monitor or scraper type
- Bug fixes in existing monitor/scraper code

### Never
- Skip verification of monitor count against website
- Submit a PR with known extraction failures
- Skip the test crawl step
- Add companies with broken or invalid board URLs
- Process more than one issue per agent run
- Push directly to main
- Commit secrets, API keys, or credentials

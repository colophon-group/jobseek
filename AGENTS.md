# AGENTS.md — Jobseek

Instructions for coding agents working on this repository.

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
│       │   └── boards.csv      # Board configs (monitor + scraper per board)
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

All crawler commands run from `apps/crawler/`:

```bash
# Setup (once per session)
alias ws='uv run ws'

# Workspace lifecycle — ws new sets the active workspace; all other commands use it automatically
ws new <slug> --issue <N>              # Create workspace + branch + draft PR (sets active)
ws use <slug>                          # Switch active workspace (multi-workspace only)
ws set --name "..." --website "..." --logo-url "..." --icon-url "..."
ws add board <alias> --url <board-url>
ws probe monitor                       # Probe all monitor types for active board
ws probe scraper                       # Probe all scraper types against sample URLs
ws select monitor <type> [--config JSON]
ws run monitor                         # Test crawl
ws select scraper <type> [--config JSON]
ws run scraper [--url URL ...]         # Test scrape sample pages
ws submit --summary "..."              # Validate, commit, push, post stats + transcript

# Rejection (before or after workspace creation)
ws reject --issue <N> --reason <key> --message "..."
ws reject --reason <key> --message "..."  # Uses active workspace's issue

# Utilities
ws validate                            # Validate CSVs
ws status                              # Show active workspace (or list all if none active)
ws use --board <alias>                 # Switch active board
ws del                                 # Remove workspace + CSV rows + close PR
ws help [topic]                        # Reference docs for monitors, scrapers, config

# Sync CSVs to database
uv run python -m src.sync
uv run python -m src.sync --dry-run

# Run the crawler (poll loop)
uv run scheduler

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

This is the primary task agents perform. Use the `ws` CLI tool for the workspace-driven flow.

### Setup

```bash
cd apps/crawler
uv sync
alias ws='uv run ws'
```

### 1. Validate the Request (pre-workspace)

Before creating a workspace, use web research to verify the request is actionable. Do **not** use crawler tooling at this stage.

**Pre-check**: grep `data/companies.csv` for the slug in the first column (to avoid matching other columns):

```bash
grep -q "^<slug>," data/companies.csv
```

If already present, comment and close the issue.

**Check 1 — Real company**: web search confirms the company exists and is operating.
**Check 2 — Public careers page**: find the careers/jobs URL by checking the company's own website (look for "Careers" or "Jobs" links). Do not rely solely on web search results — they may be stale or point to the wrong ATS. Fetch the company's careers page directly to discover the current board URL.
**Check 3 — At least one listing visible**: the career page shows job postings.

**On any failure**, reject with reason key (`not-a-company`, `company-not-found`, `no-job-board`, `no-open-positions`):

```bash
ws reject --issue 42 --reason no-job-board --message "No public careers page found for Acme Corp"
```

**Edge cases**:
- Ambiguous name with no URL → `company-not-found`
- Careers page behind auth → `no-job-board`
- Unusual format (PDF, iframe) → proceed, monitor/scraper will handle it
- Small company (1–3 jobs) → valid, proceed

### 2. Claim the Issue

`ws new` handles: gh auth check, existing PR check, branch creation, stub CSV row, commit, push, draft PR. It also sets the active workspace, so all subsequent commands auto-resolve the slug.

```bash
ws new stripe --issue 42
```

### 3. Research and Set Company Details

```bash
ws set --name "Stripe" --website "https://stripe.com" \
  --logo-url "https://stripe.com/img/logo.svg" \
  --icon-url "https://www.google.com/s2/favicons?domain=stripe.com&sz=128"
```

URLs are advisory-checked (reachability, image content type) but always saved.

### 4. Add Board and Probe Monitors

```bash
ws add board careers --url "https://boards.greenhouse.io/stripe"
ws probe monitor
```

`add board` auto-prefixes the alias with the company slug (`careers` → `stripe-careers`) and auto-activates the board. `probe monitor` tries all monitor types and reports results.

### 5. Select and Test Monitor

```bash
ws select monitor greenhouse
ws run monitor
```

After the test crawl, compare the job count against the website's displayed total. If counts don't match, iterate:

```bash
ws select monitor sitemap
ws run monitor
```

**Zero jobs**: Step 1 confirmed listings exist, so 0 results indicates misconfiguration. Debug systematically — try different monitor types, check API tokens, verify the URL.

### 6. Select and Test Scraper (non-API monitors only)

API monitors (`greenhouse`, `lever`, `ashby`) return full data — `ws run monitor` prints "Skipping scraper" and auto-marks scraper steps as done. `api_sniffer` with auto-mapped `fields` also skips the scraper step.

For URL-only monitors (`sitemap`, `dom`, `api_sniffer` without `fields`), start by probing all scraper types:

```bash
ws probe scraper
```

This tries all scraper types with heuristic auto-config against sample URLs and shows a quality comparison.

**Before selecting, evaluate probe results critically:**
- Do not follow the "Next:" suggestion if required fields show 0/N — the heuristic config is wrong. A scraper that can't extract titles or descriptions will never produce complete data regardless of other settings.
- If the probe warns about JS-rendered pages (SPA warning), check the page source for embedded structured data (script tags, inline JSON) before trying `render: true` with DOM scraper. The data you need may exist in a format the probe doesn't test — completeness depends on finding the right data source.
- For dom scraper, inspect `flat.json` to verify DOM element order before writing steps. Steps must follow DOM order (the cursor only moves forward) — wrong order silently skips fields, undermining reliability.

If no scraper auto-detects and the page source contains structured JSON data in `<script>` tags or JS variable assignments, use the `embedded` scraper with manual config (`script_id`, `pattern`, or `variable`). This is more resilient than DOM scraping for sites with embedded structured data.

Then select the best one:

```bash
ws select scraper json-ld --config '<from probe>'
ws run scraper
```

Check the extraction quality table. If fields are missing, iterate with a different type or config.

### 6b. Verify Extraction Quality

Before submitting, verify that extracted content is **complete and correct**, not just populated. A field counting N/N does not mean the data is right — completeness requires actual content verification:

- Read the content samples in `ws run scraper` output. A populated field is not necessarily a correct field — verify the actual text makes sense and isn't truncated, garbled, or generic. For example, locations showing "+2 more" means incomplete data even if the field counts as populated.
- If extracted content looks incomplete, investigate the page source for better data sources. Don't apply regex cleanup to broken data — find where the complete data lives. Reliability comes from extracting complete data at the source, not patching partial data downstream.
- Check for additional mappable fields in the raw data source (same data source, no additional requests): `employment_type`, `date_posted`, `job_location_type`, team/department (as `metadata.*`), `base_salary`, `qualifications`, `responsibilities`.
- When structured data exists in the page source, try the `embedded` scraper (`ws help scraper embedded`) before DOM scraping or code changes. It handles `<script>` tags, JS variables, and callback patterns. Only escalate to code changes if no existing scraper can parse the data.

**Per scraper type:**
- **nextdata**: Read the `nextdata.json` or scraper-probe artifact to see all available keys in each item, then extend `fields` mapping.
- **dom**: Inspect `sample-N.html`, `flat.json`, or scraper-probe artifacts for additional structured content near extracted fields.
- **json-ld**: No action needed — json-ld automatically extracts all standard JobPosting properties.
- **embedded**: Inspect page source for `<script id="...">` tags with JSON, JS variable assignments (`window.__DATA__ = {...}`), or callback patterns (`AF_initDataCallback`). Use browser DevTools (Elements → search for `<script`) to find the data source, then configure `script_id`, `pattern`, or `variable` accordingly. Check all available keys in the JSON and map additional fields.
- **api_sniffer**: Auto-probed via Playwright in `ws probe scraper`. Detects pages that load job data via XHR/fetch. If probe shows quality stats, use the suggested config. If content is server-rendered (not loaded via XHR), use json-ld or dom instead.

Run `ws run scraper` again after config changes to verify new fields appear and content quality is correct.

### 7. Submit

`ws submit` handles: CSV write, validation, commit, push, crawl stats comment, mark PR ready, transcript comment.

```bash
ws submit --summary "..."
```

The `--summary` should focus on **difficulties, roadblocks, or unexpected behaviors** encountered during configuration — not just restate the final result. If everything went smoothly, say so briefly. Examples:

- `"Straightforward greenhouse config, 138 jobs"` — nothing notable happened
- `"Sitemap had 200 URLs but only 40 were job pages; rest were blog posts. Used path filter."` — unexpected sitemap content
- `"Auto-detect returned lever but token was wrong; had to extract correct token from page source."` — detection worked but config needed manual adjustment
- `"Tried sitemap (0 jobs — sitemap only has blog posts), then dom monitor worked. JSON-LD scraper missing locations, switched to dom scraper with render: false."` — multiple iterations needed

### 8. Escalate to Code Changes (when needed)

If no existing monitor/scraper type works after exhausting config options:

1. Use `ws del` to clean up the config-only workspace
2. Create a new PR on a `fix-crawler/<description>` branch manually
3. Reference what was tried in the PR body
4. Include both the code change and CSV config in the same PR

### Full Example

```bash
alias ws='uv run ws'

# Claim (sets active workspace — no need to repeat the slug after this)
ws new stripe --issue 42

# Configure
ws set --name "Stripe" --website "https://stripe.com" \
  --logo-url "https://stripe.com/img/logo.svg" \
  --icon-url "https://www.google.com/s2/favicons?domain=stripe.com&sz=128"

# Board + monitor
ws add board careers --url "https://boards.greenhouse.io/stripe"
ws probe monitor
ws select monitor greenhouse
ws run monitor

# Submit (~8 total commands)
ws submit --summary "Straightforward greenhouse config, 138 jobs"
```

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
company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config
```

- `company_slug`: must exist in companies.csv
- `board_slug`: unique identifier in `{company}-{alias}` format (e.g., `stripe-careers`)
- `board_url`: unique career page URL
- `monitor_type`: `ashby` | `greenhouse` | `lever` | `sitemap` | `nextdata` | `dom` | `api_sniffer`
- `monitor_config`: JSON string (use `""` for inner quotes)
- `scraper_type`: `ashby_api` | `greenhouse_api` | `lever_api` | `json-ld` | `dom` | `nextdata` | `embedded` | `api_sniffer` (empty for API monitors)
- `scraper_config`: JSON string (empty for json-ld, greenhouse_api, lever_api)

## Job Data Fields

- **Required**: `title`, `description` (HTML) — must be N/N, do not submit with 0/N
- **Important**: `locations`, `job_location_type` — missing locations acceptable only if `job_location_type` is set (e.g. remote-only companies)
- **Optional**: `employment_type`, `date_posted`, `base_salary`, `skills`, `qualifications`, `responsibilities`

`ws run scraper` shows extraction stats. Titles and descriptions should be N/N.
0/N titles or 0/N descriptions means wrong scraper type or config — do not submit.

## Configuration Priorities

When choosing between monitor/scraper configurations, optimize in this order:

1. **Coverage** — all jobs on the board must be discovered. Full coverage always takes priority. If a cheaper monitor finds fewer jobs, use the one with full coverage.
2. **Required fields** — title and description must extract for every job. 0/N on either is a blocker.
3. **Resilience** — prefer configurations that won't break when the site changes. This is valued higher than speed. Resilient choices: API monitors > sitemap > dom; json-ld scraper > dom scraper; `render: false` > `render: true`; simple configs > complex configs with many selectors.
4. **Important fields** — locations and job_location_type should extract when available. Missing locations are acceptable only for remote-only companies where job_location_type is populated.
5. **Speed/cost** — among equivalent configs, prefer cheaper monitors and `render: false`. But never sacrifice coverage or resilience for speed.
6. **Optional fields** — more is better, but not at the cost of resilience or speed.

### Key rules

- **Always prefer `render: false`** when content loads without JavaScript. Only use `render: true` when static fetch produces empty or incomplete results.
- **API monitors are most resilient** — Ashby/Greenhouse/Lever APIs are stable and return rich data. Always use them when detected.
- **json-ld scraper is more resilient than dom** — schema.org markup is standardized. Try json-ld before dom for any URL-only monitor.
- **Multi-board companies**: configure all career pages unless one board's listings are a strict superset of another's. When in doubt, configure both.
- **Low quality after exhausting config options**: if extraction quality remains poor after trying all applicable monitor/scraper combinations, escalate to code changes (`ws del`, then `fix-crawler/` branch). Document what was tried.
- **api_sniffer bridges the gap** — when no known ATS API exists but the site loads data via internal APIs, api_sniffer captures those APIs. With `fields` auto-mapped it acts like an API monitor (scraper skipped). More resilient than dom for API-driven sites.
- **Resilience is subjective** — optimizing for it requires case-by-case judgment. Simpler configurations that rely on stable structures (APIs, sitemaps, schema.org) are preferred over complex step-based selectors that may break on site redesigns.

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
- PR body: managed automatically by `ws new` and `ws submit`
- Never push directly to main — always create a PR

## Boundaries

### Always (do without asking)
- Validate CSVs before committing
- Test crawl before submitting PR
- Verify crawled job count against the website's displayed total
- Test scraper extraction on 2–3 sample URLs before committing
- Follow the CSV schemas exactly

### Ask (check with maintainer first)
- Changing database schema
- Modifying CI/CD workflows
- Adding new dependencies

### Escalate (propose code changes via PR)
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

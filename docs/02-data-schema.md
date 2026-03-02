# Data Schema

CSV files are the source of truth for company and board configuration. The database is derived state, rebuilt from CSVs on each deploy via the sync script.

## data/companies.csv

```csv
slug,name,website,logo_url,icon_url
stripe,Stripe,https://stripe.com,https://stripe.com/img/logo.svg,https://stripe.com/favicon.ico
meta,Meta,https://meta.com,https://...,https://...
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `slug` | Yes | Primary key. Lowercase, URL-safe. Generated from company name. |
| `name` | Yes | Display name (official or commonly known). |
| `website` | Yes | Company homepage URL. |
| `logo_url` | No | Direct URL to company logo image file. |
| `icon_url` | No | Direct URL to favicon/icon image file. |

### Rules

- `slug` must be unique across all rows
- `slug` format: lowercase alphanumeric + hyphens, no leading/trailing hyphens
- `website` must be a valid URL with scheme (https preferred)
- `logo_url` and `icon_url` should point to actual image files, not pages containing images
- Git history provides `created_at` / `updated_at` timestamps — no need for CSV columns

## data/boards.csv

```csv
company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config
stripe,https://boards.greenhouse.io/stripe,greenhouse,"{""token"":""stripe""}",greenhouse_api,
meta,https://www.metacareers.com/jobs,sitemap,"{""sitemap_url"":""https://...""}",json-ld,
google,https://careers.google.com/jobs,discover,"{""wait"":""networkidle""}",html,"{""title"":""h2.p1N2lc"",""location"":""span.vo5qdf""}"
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `company_slug` | Yes | Foreign key to companies.csv `slug`. |
| `board_url` | Yes | The career page URL to monitor. |
| `monitor_type` | Yes | How to discover listings. One of: `greenhouse`, `lever`, `sitemap`, `discover`. |
| `monitor_config` | No | JSON object with monitor-specific settings. |
| `scraper_type` | No | How to extract job details. One of: `greenhouse_api`, `lever_api`, `json-ld`, `html`, `browser`. Empty when monitor provides full data. |
| `scraper_config` | No | JSON object with scraper-specific settings. |

### Rules

- `company_slug` must reference an existing row in companies.csv
- `board_url` must be unique across all rows
- `monitor_config` and `scraper_config` are JSON strings (use `""` for quotes inside CSV)
- API monitors (`greenhouse`, `lever`) typically don't need a `scraper_type` — the monitor returns full job data
- URL-only monitors (`sitemap`, `discover`) require a `scraper_type`

### Monitor + Scraper Pairing

| Monitor Type | Returns | Scraper Needed? | Typical Scraper |
|-------------|---------|-----------------|-----------------|
| `greenhouse` | Full job data | No | `greenhouse_api` (passthrough) |
| `lever` | Full job data | No | `lever_api` (passthrough) |
| `sitemap` | URLs only | Yes | `json-ld` or `html` |
| `discover` | URLs only | Yes | `json-ld`, `html`, or `browser` |

See [04 — Monitors and Scrapers](./04-monitors-and-scrapers.md) for config details per type.

## DB Sync

The sync script (`src/sync.py`) runs on deploy and reads both CSVs to upsert database rows.

### Sync Behavior

```
CSV → DB rules:
  companies.csv  → company table (upsert on slug)
  boards.csv     → job_board table (upsert on board_url)
```

- **New rows**: Inserted with defaults (next_check_at = now, is_enabled = true)
- **Existing rows**: Config fields updated, runtime fields preserved:
  - Preserved: `next_check_at`, `last_checked_at`, `last_success_at`, `consecutive_failures`, `last_error`, `is_enabled`
  - Updated: `crawler_type`, `metadata` (from monitor_config), company fields
- **Removed rows**: Boards not in CSV are disabled (`is_enabled = false`), not deleted. This preserves historical job posting data.

### Running the Sync

```bash
cd apps/crawler
uv run python -m src.sync              # sync both CSVs
uv run python -m src.sync --dry-run    # show what would change without writing
```

## CSV Validation

The validate script (`src/validate.py`) checks CSV integrity. It runs in CI and agents use it before committing.

```bash
cd apps/crawler
uv run python -m src.validate                        # validate CSVs
uv run python -m src.validate --detect <url>         # auto-detect monitor type for a URL
uv run python -m src.validate --probe-jsonld <url>   # check if a job page has JSON-LD
uv run python -m src.validate --test-monitor <slug> <board-url>  # test crawl a board
```

### Validation Checks

- All slugs are valid format (lowercase alphanumeric + hyphens)
- All slugs in boards.csv exist in companies.csv
- No duplicate slugs in companies.csv
- No duplicate board_urls in boards.csv
- All URLs are valid and have a scheme
- monitor_config and scraper_config are valid JSON (when present)
- Required scraper_type present when monitor_type is url-only

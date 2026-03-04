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
company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config
stripe,stripe-careers,https://boards.greenhouse.io/stripe,greenhouse,"{""token"":""stripe""}",,
meta,meta-careers,https://www.metacareers.com/jobs,sitemap,"{""sitemap_url"":""https://...""}",json-ld,
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `company_slug` | Yes | Foreign key to companies.csv `slug`. |
| `board_slug` | Yes | Unique board identifier in `{company}-{alias}` format. |
| `board_url` | Yes | The career page URL to monitor. |
| `monitor_type` | Yes | How to discover listings. One of: `greenhouse`, `lever`, `sitemap`, `nextdata`, `dom`. |
| `monitor_config` | No | JSON object with monitor-specific settings. |
| `scraper_type` | No | How to extract job details. One of: `json-ld`, `dom`, `nextdata`, `embedded`, `api_sniffer`. Empty when API monitor provides full data. |
| `scraper_config` | No | JSON object with scraper-specific settings. |

### Rules

- `company_slug` must reference an existing row in companies.csv
- `board_slug` must be unique across all rows and match slug format
- `board_url` must be unique across all rows
- `monitor_config` and `scraper_config` are JSON strings (use `""` for quotes inside CSV)
- **Rich monitors** (all API types: ashby, greenhouse, hireology, lever, personio, pinpoint, recruitee, rippling, rss, smartrecruiters, workable, workday; also `api_sniffer` with auto-mapped `fields`) return full job data — `scraper_type` is empty
- **URL-only monitors** (`sitemap`, `dom`, `nextdata`, `api_sniffer` without `fields`) return URLs only — `scraper_type` is required

### Monitor + Scraper Pairing

| Monitor Type | Returns | Scraper Needed? | Typical Scraper |
|-------------|---------|-----------------|-----------------|
| API monitors (greenhouse, lever, ashby, etc.) | Full job data | No | *(empty)* |
| `api_sniffer` with `fields` | Full job data | No | *(empty)* |
| `sitemap` | URLs only | Yes | `json-ld` or `dom` |
| `nextdata` | URLs only | Yes | `nextdata` or `json-ld` |
| `dom` | URLs only | Yes | `json-ld`, `dom`, or `embedded` |
| `api_sniffer` without `fields` | URLs only | Yes | `json-ld`, `dom`, or `embedded` |

> **Note:** During workspace development, multiple configurations can be tested and stored as named configs in the workspace YAML. At submit time, only the active config is written to the CSV row.

See [04 — Monitors and Scrapers](./04-monitors-and-scrapers.md) for config details per type. See [08 — Job Data Fields](./08-job-data-fields.md) for the complete field reference (types, formats, per-ATS source mapping).

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

The `ws validate` command checks CSV integrity. It runs in CI and agents use it before submitting.

```bash
cd apps/crawler
alias ws='uv run ws'
ws validate                            # validate CSVs
ws probe                               # probe all monitor types for active board
ws run monitor                         # test crawl active board
ws run scraper                         # test scrape sample pages
```

### Validation Checks

- All slugs are valid format (lowercase alphanumeric + hyphens)
- All slugs in boards.csv exist in companies.csv
- No duplicate slugs in companies.csv
- No duplicate board_slugs in boards.csv
- No duplicate board_urls in boards.csv
- All URLs are valid and have a scheme
- monitor_config and scraper_config are valid JSON (when present)
- Required scraper_type present when monitor_type is url-only

# Data — CSV Config Files

Source of truth for all tracked companies and their board configurations. The database is derived state, rebuilt from these files on each deploy via `uv run python -m src.sync`.

## Files

### companies.csv

Company registry. One row per company.

| Column | Required | Description |
|--------|----------|-------------|
| `slug` | Yes | Unique URL-safe identifier (e.g. `stripe`). Primary key. |
| `name` | Yes | Display name (e.g. `Stripe`). |
| `website` | Yes | Company homepage URL. |
| `logo_url` | No | Direct URL to logo image file. |
| `icon_url` | No | Direct URL to favicon/icon image file. |

### boards.csv

Board configurations. One row per job board. A company can have multiple boards.

| Column | Required | Description |
|--------|----------|-------------|
| `company_slug` | Yes | References `slug` in companies.csv. |
| `board_url` | Yes | Career page URL (unique). |
| `monitor_type` | Yes | How to discover listings: `greenhouse`, `lever`, `sitemap`, `discover`. |
| `monitor_config` | No | JSON string with monitor-specific settings. |
| `scraper_type` | No | How to extract details: `greenhouse_api`, `lever_api`, `json-ld`, `html`, `browser`. |
| `scraper_config` | No | JSON string with scraper-specific settings. |

## Adding a Company

1. Add a row to `companies.csv`
2. Add one or more rows to `boards.csv`
3. Validate: `uv run python -m src.validate`

Keep rows sorted by slug. JSON configs use standard CSV quoting (wrap in double quotes, escape inner quotes by doubling them).

### Example

companies.csv:
```
stripe,Stripe,https://stripe.com,https://stripe.com/img/logo.svg,https://stripe.com/favicon.ico
```

boards.csv:
```
stripe,https://boards.greenhouse.io/stripe,greenhouse,"{""token"":""stripe""}",greenhouse_api,
```

## Reading in Python

```python
import polars as pl

companies = pl.read_csv("data/companies.csv")
boards = pl.read_csv("data/boards.csv")
```

## Documentation

See [docs/02-data-schema.md](../../docs/02-data-schema.md) for full schema documentation.

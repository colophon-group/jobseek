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
| `board_slug` | Yes | Unique board identifier in `{company}-{alias}` format (e.g. `stripe-careers`). |
| `board_url` | Yes | Career page URL (unique). |
| `monitor_type` | Yes | How to discover listings: `greenhouse`, `lever`, `sitemap`, `nextdata`, `dom`. |
| `monitor_config` | No | JSON string with monitor-specific settings. |
| `scraper_type` | No | How to extract details: `json-ld`, `dom`, `nextdata`, `embedded`, `api_sniffer`. Empty for API monitors. |
| `scraper_config` | No | JSON string with scraper-specific settings. |

## Adding a Company

Use the `ws` CLI tool:

```bash
alias ws='uv run ws'
ws new stripe --issue 42
ws set stripe --name "Stripe" --website "https://stripe.com"
ws add board stripe careers --url "https://boards.greenhouse.io/stripe"
ws probe stripe
ws select monitor stripe greenhouse
ws run monitor stripe
ws submit stripe --summary "Configured greenhouse monitor (138 jobs)"
```

Or manually: add rows to both CSVs, then validate with `ws validate`.

Keep rows sorted by slug. JSON configs use standard CSV quoting (wrap in double quotes, escape inner quotes by doubling them).

### Example

companies.csv:
```
stripe,Stripe,https://stripe.com,https://stripe.com/img/logo.svg,https://stripe.com/favicon.ico
```

boards.csv:
```
stripe,stripe-careers,https://boards.greenhouse.io/stripe,greenhouse,"{""token"":""stripe""}",,
```

## Reading in Python

```python
import polars as pl

companies = pl.read_csv("data/companies.csv")
boards = pl.read_csv("data/boards.csv")
```

## Documentation

See [docs/02-data-schema.md](../../docs/02-data-schema.md) for full schema documentation.

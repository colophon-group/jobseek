---
type: case-study
company: glencore
monitor: mixed
scraper: mixed
summary: "7 boards across 6 ATS platforms requiring platform-specific configs"
tags: [multi-ats, workday, rss, lever, greenhouse, api_sniffer, dom, large-company]
---
# Glencore — 7 boards across 6 ATS platforms

## Setup
- 7 boards across 6 different ATS platforms
- Monitor types: workday (2), rss (1), lever (1), greenhouse (1), api_sniffer (1), dom (1)
- Scraper types: skip (rich monitors), json-ld (dom board), skip (all others)

## Key decisions
- Glencore operates across multiple subsidiaries and divisions, each using its own ATS platform
  — no single monitor type covers more than 2 boards
- Workday is used by two divisions: Astron Energy and Coal, each with separate Workday tenants
  requiring independent board configurations
- SuccessFactors (Glencore Copper) exposed an RSS/Google feed (`/googlefeed.xml`) — using RSS
  monitor with `preset: "successfactors"` provides richer data than DOM scraping at lower cost
  (no browser, no per-page requests)
- Lever is used by the EVR subsidiary with a standard token — Lever's API returns complete job
  data, so scraper is skipped
- Greenhouse is used for UK operations — standard token-based monitor with full data
- The main api_sniffer board aggregates most divisions' jobs from the primary glencore.com
  careers API, covering divisions that don't have dedicated ATS platforms
- NGA Australia uses a DOM monitor with `render: true` — initially missed during discovery
  and added after user intervention. Some job detail pages return HTTP 403, but the majority
  succeed; verdict: acceptable coverage given the small board size (~40 jobs)
- Key tradeoff: RSS for SuccessFactors was preferred over DOM because it provides structured
  data (title, description, locations, date_posted) without per-page scraping overhead

## Board breakdown
| Board alias        | Monitor       | Scraper  | Notes                              |
|--------------------|---------------|----------|-------------------------------------|
| glencore-main      | api_sniffer   | skip     | Primary careers API, ~2,100 jobs    |
| glencore-astron    | workday       | skip     | Astron Energy, Workday tenant       |
| glencore-coal      | workday       | skip     | Coal division, Workday tenant       |
| glencore-copper    | rss           | skip     | SuccessFactors googlefeed.xml       |
| glencore-evr       | lever         | skip     | EVR subsidiary, standard Lever      |
| glencore-uk        | greenhouse    | skip     | UK operations, Greenhouse token     |
| glencore-nga       | dom           | json-ld  | NGA Australia, render: true         |

## Config
```json
{
  "monitor_config (glencore-main)": {
    "api_url": "https://www.glencore.com/api/careers/search?limit=100&offset=0",
    "method": "GET",
    "json_path": "results",
    "pagination": {"param_name": "offset", "style": "offset", "increment": 100},
    "fields": {
      "title": "title",
      "locations": "location",
      "description": "description",
      "date_posted": "publishedDate"
    }
  },
  "monitor_config (glencore-copper)": {
    "feed_url": "https://jobs.glencore.com/copper/googlefeed.xml",
    "preset": "successfactors"
  },
  "monitor_config (glencore-evr)": {
    "token": "evr-metals"
  },
  "monitor_config (glencore-nga)": {
    "board_url": "https://nga.net.au/careers/",
    "render": true,
    "job_link_pattern": "nga\\.net\\.au/careers/\\d+/"
  },
  "scraper_config (glencore-nga)": {}
}
```

## Lesson
Large conglomerate companies with multiple subsidiaries often require the widest variety of
ATS integrations. Don't assume subsidiaries share a common careers platform — each may have
been acquired with its own ATS already in place. When mapping boards, start with the parent
company's main careers page (often an api_sniffer or DOM board), then systematically check
each subsidiary. Prefer RSS feeds for SuccessFactors over DOM scraping — RSS provides
structured data at lower cost. Accept partial coverage on small subsidiary boards (e.g., 403
errors on some pages) when the board has few jobs and the alternative is no coverage at all.

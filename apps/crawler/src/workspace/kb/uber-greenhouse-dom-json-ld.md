---
type: case-study
company: uber
monitor: mixed
scraper: json-ld
summary: "4 boards: Greenhouse main + 3 iCIMS regional with static fetch workaround"
tags: [greenhouse, icims, dom, render, multi-ats, regional]
---
# Uber — Greenhouse main board with iCIMS regional boards using static DOM

## Setup
- 4 boards: 1 Greenhouse (main) + 3 iCIMS regional (EMEA, APAC, India)
- Monitor: greenhouse (main), dom (regional iCIMS boards with render: false)
- Scraper: skip (Greenhouse), json-ld (iCIMS boards)

## Key decisions
- Uber's primary careers board uses Greenhouse with token "uber" — standard Greenhouse
  monitor with full data extraction, no scraper needed
- Regional boards on uber.icims.com serve EMEA, APAC, and India job listings separately
  from the main Greenhouse board
- iCIMS boards required `render: false` (static HTML fetch) because Playwright rendering
  produced inconsistent results — the JavaScript-heavy iCIMS frontend yields different job
  counts on successive renders, sometimes returning 0 jobs
- Static fetch reliably captures the server-rendered job links from iCIMS pages
- The api_sniffer initially captured Uber's internal API, but the API response lacked
  location data entirely — making it unsuitable as a primary source
- Instead of using the incomplete API data, the agent chose dom monitor (URL-only mode)
  plus json-ld scraper to get full job details including locations from the detail pages
- JSON-LD on iCIMS detail pages provides complete JobPosting schema with title, description,
  locations, employment_type, and date_posted

## Board breakdown
| Board alias     | Monitor     | Scraper | Notes                              |
|-----------------|-------------|---------|-------------------------------------|
| uber-main       | greenhouse  | skip    | Token: uber, ~3,500 jobs            |
| uber-emea       | dom         | json-ld | iCIMS, render: false, ~800 jobs     |
| uber-apac       | dom         | json-ld | iCIMS, render: false, ~600 jobs     |
| uber-india      | dom         | json-ld | iCIMS, render: false, ~400 jobs     |

## Config
```json
{
  "monitor_config (uber-main)": {
    "token": "uber"
  },
  "monitor_config (uber-emea)": {
    "board_url": "https://uber.icims.com/jobs/search?ss=1&searchLocation=12781--EMEA",
    "render": false,
    "job_link_pattern": "uber\\.icims\\.com/jobs/\\d+/",
    "pagination": {"param_name": "pr", "style": "page"}
  },
  "scraper_config (uber-emea)": {}
}
```

## Lesson
When Playwright rendering produces inconsistent results on an ATS (varying job counts,
intermittent empty pages), try `render: false` for static HTML fetch before abandoning
the DOM approach entirely. iCIMS in particular often renders job links in the initial
server HTML, making static fetch both more reliable and faster. When an API capture lacks
critical fields (like locations), prefer dom + json-ld over accepting incomplete data —
URL-only discovery with JSON-LD scraping often provides the most complete field coverage.

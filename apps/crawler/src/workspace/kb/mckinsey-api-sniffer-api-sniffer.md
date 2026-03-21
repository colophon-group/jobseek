---
type: case-study
company: mckinsey
monitor: api_sniffer
scraper: skip
summary: "Cloudflare-protected API with stealth mode and 3-field description via list spec"
tags: [cloudflare, stealth, list-spec, description-split, constant-headers, base-salary]
---
# McKinsey — Cloudflare-protected API with stealth mode

## Setup
- Monitor: api_sniffer (browser mode with `stealth: true` to bypass Cloudflare)
- Scraper: skip (monitor provides all data via rich `fields` mapping)

## Key decisions
- Cloudflare blocks standard headless browsers — solved with `stealth: true`
- Description split across 3 fields: `whatYouWillDo`, `whoYouWillWorkWith`, `yourBackground`
- Used list spec with `=` constant headers for section titles:
  `["=<h3>What You Will Do</h3>", "whatYouWillDo", "=<h3>Who You Will Work With</h3>", ...]`
- Compensation uses list spec on `base_salary`: `["=Salary range: ", "compensationRange"]`
  — the `=` prefix is a constant string prepended to the salary text
- No separate scraper needed — monitor `fields` mapping extracts everything

## Config
```json
{
  "monitor_config": {
    "api_url": "https://gateway.mckinsey.com/.../v1/api/jobs/search?pageSize=1000&start=1&lang=en",
    "method": "GET",
    "json_path": "docs",
    "browser": true,
    "stealth": true,
    "fields": {
      "title": "title",
      "locations": "cities",
      "description": [
        "=<h3>What You Will Do</h3>", "whatYouWillDo",
        "=<h3>Who You Will Work With</h3>", "whoYouWillWorkWith",
        "=<h3>Your Background</h3>", "yourBackground"
      ],
      "base_salary": ["=Salary range: ", "compensationRange"]
    }
  }
}
```

## Lesson
When Cloudflare protection blocks headless browser requests, enable `stealth: true`.
For multi-field descriptions, use a list spec with `=` constant strings as section headers.
The `=` prefix emits a literal string; it's only included when the following expression
resolves to non-null data.

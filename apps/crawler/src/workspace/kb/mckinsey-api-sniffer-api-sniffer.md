---
type: case-study
company: mckinsey
monitor: api_sniffer
scraper: api_sniffer
summary: "Cloudflare-protected API with stealth mode and 3-field description concat"
tags: [cloudflare, stealth, list-concat, description-split, compensation-range, context-prefix]
---
# McKinsey — Cloudflare-protected API with stealth mode

## Setup
- Monitor: api_sniffer (browser mode with `stealth: true` to bypass Cloudflare)
- Scraper: api_sniffer (detail API enrichment)

## Key decisions
- Cloudflare blocks standard headless browsers — solved with `stealth: true` (`--headless=new` Chrome flag)
- Description split across 3 fields: `description`, `qualifications`, `whatYoullDo` — joined via `list_concat`
- Compensation uses `compensationRange` field with min/max values
- Used `context` prefix on `base_salary` to preserve the compensation label text alongside the range

## Config
```json
{
  "monitor_config": {
    "stealth": true
  },
  "scraper_config": {
    "fields": {
      "description": {
        "list_concat": [
          {"path": "description"},
          {"path": "qualifications", "wrap": "<h3>Qualifications</h3>\n{value}"},
          {"path": "whatYoullDo", "wrap": "<h3>What You'll Do</h3>\n{value}"}
        ]
      },
      "base_salary": {"path": "compensationRange", "context": "Compensation"}
    }
  }
}
```

## Lesson
When Cloudflare protection blocks headless browser requests, enable `stealth: true`
in monitor config. For multi-field descriptions, `list_concat` with `wrap` templates
keeps section headings intact. Use `context` on salary fields to preserve labels.

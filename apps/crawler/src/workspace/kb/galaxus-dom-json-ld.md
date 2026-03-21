---
type: case-study
company: galaxus
monitor: dom
scraper: json-ld
summary: "JSON-LD with DOM fallback for description-only enrichment"
tags: [json-ld, fallback, dom, description-enrichment, field-level-fallback]
---
# Galaxus — JSON-LD with field-level DOM fallback for descriptions

## Setup
- Monitor: dom (static HTML link discovery)
- Scraper: json-ld with `fallback` to DOM scraper for description field

## Key decisions
- JSON-LD on job pages provides title, locations, date_posted, and employment_type cleanly
- However, JSON-LD descriptions are truncated or missing on some pages
- Instead of switching entirely to DOM scraper, used `fallback` config to only fall back
  for the `description` field
- Fallback DOM config uses a German text anchor (`beweg` — short for "Bewerbung") to find
  the description section, stopping at "Bewerbung & Kontakt"
- `fields: ["description"]` in fallback limits which fields trigger the fallback — all other
  fields still come from JSON-LD

## Config
```json
{
  "scraper_config": {
    "fallback": {
      "type": "dom",
      "config": {
        "steps": [
          {
            "tag": "h3",
            "text": "beweg",
            "field": "description",
            "html": true,
            "stop": "Bewerbung & Kontakt"
          }
        ]
      },
      "fields": ["description"]
    }
  }
}
```

## Lesson
When a primary scraper (like json-ld) handles most fields well but fails on specific ones,
use the `fallback` config with a `fields` list to selectively fall back to another scraper
type for just those fields. This gives you the best of both worlds — clean structured data
from JSON-LD plus robust extraction for the problematic fields.

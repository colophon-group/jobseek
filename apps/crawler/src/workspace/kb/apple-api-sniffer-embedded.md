---
type: case-study
company: apple
monitor: api_sniffer
scraper: embedded
summary: "Boolean homeOffice field mapped to job_location_type via map spec"
tags: [boolean-map, job-location-type, map-spec, field-mapping]
---
# Apple — Boolean homeOffice mapped to job_location_type

## Setup
- Monitor: api_sniffer
- Scraper: embedded

## Key decisions
- Job data includes a boolean `homeOffice` field (true/false) instead of a string location type
- Used `map` spec to convert the boolean to a valid `job_location_type` value
- Map spec: `{"path": "homeOffice", "map": {"True": "remote"}}` — only maps `True`; `False` produces no value (on-site is the default)
- This avoids needing a custom code change for a simple boolean→enum conversion

## Config
```json
{
  "scraper_config": {
    "fields": {
      "job_location_type": {
        "path": "homeOffice",
        "map": {"True": "remote"}
      }
    }
  }
}
```

## Lesson
When a field contains boolean or non-standard values that need to map to an enum,
use the `map` spec in field config. Only map the values that should produce output —
unmapped values (like `False` for on-site) naturally produce no value, which is the
correct default behavior.

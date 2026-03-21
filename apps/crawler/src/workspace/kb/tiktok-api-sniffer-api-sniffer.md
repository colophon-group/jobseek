---
type: case-study
company: tiktok
monitor: api_sniffer
scraper: api_sniffer
summary: "HTTP-mode api_sniffer with detail API for compensation HTML and salary parsing"
tags: [api_sniffer, http-mode, detail-api, salary-parse, post-body, id-placeholder]
---
# TikTok — HTTP-mode api_sniffer with detail API

## Setup
- Monitor: api_sniffer (HTTP mode, POST request to list endpoint)
- Scraper: api_sniffer (HTTP mode, POST request to detail endpoint with `{id}` placeholder)

## Key decisions
- List API returns description and requirement as separate fields — concatenated in scraper
- Detail API uses POST with `{id}` placeholder in `post_body` JSON
- Compensation data is HTML in `job_post_object_value_list[0].field_value`
- Salary is parseable from the compensation text via `base_salary` field with `parse_salary_text` auto-conversion
- The `{id}` placeholder in `post_body` is replaced at runtime with each job's ID

## Config
```json
{
  "monitor_config": {
    "api_url": "https://careers.tiktok.com/api/v1/search/job",
    "method": "POST",
    "post_body": "{\"limit\": 100, \"offset\": 0}",
    "pagination": {"param": "offset", "increment": 100}
  },
  "scraper_config": {
    "api_url": "https://careers.tiktok.com/api/v1/job/detail/{id}",
    "method": "POST",
    "post_body": "{\"job_id\": \"{id}\"}",
    "fields": {
      "description": {
        "list_concat": [
          {"path": "description"},
          {"path": "requirement"}
        ]
      },
      "base_salary": {"path": "job_post_object_value_list[0].field_value"}
    }
  }
}
```

## Lesson
When a list API splits content across multiple fields (description vs requirement),
use `list_concat` in the scraper to join them. For compensation data embedded as
HTML text, `base_salary` with `parse_salary_text` can extract structured salary info.

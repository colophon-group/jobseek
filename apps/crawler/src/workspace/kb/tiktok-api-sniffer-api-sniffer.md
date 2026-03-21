---
type: case-study
company: tiktok
monitor: api_sniffer
scraper: api_sniffer
summary: "HTTP-mode api_sniffer with detail API for description enrichment and salary parsing"
tags: [api_sniffer, http-mode, detail-api, salary-parse, post-body, id-placeholder, enrich, concat-separator, location-country]
---
# TikTok — HTTP-mode api_sniffer with detail API

## Setup
- Monitor: api_sniffer (POST to list endpoint, browser mode with body pagination)
- Scraper: api_sniffer (POST to detail endpoint with `{id}` placeholder, HTTP mode)

## Key decisions
- List API returns titles and locations; description split across `description` and `requirement`
- Locations use `concat` spec with `separator: ", "` to produce `"Warsaw, Poland"` from
  nested `city_info.en_name` + `city_info.parent.parent.en_name` — avoids ambiguous city-only
  strings like "San Jose" that fail location resolution
- Used list spec with `=` constant headers to concatenate: `["=<h3>Responsibilities</h3>", "description", "=<h3>Qualifications</h3>", "requirement"]`
- `enrich: ["description"]` — monitor has partial descriptions, scraper fetches full ones from detail API
- Detail API uses POST with `{id}` placeholder in `post_body` JSON, replaced at runtime per job
- Salary available as `job_post_info.job_post_object_value_list[0].field_value` — parsed automatically by `parse_salary_text` when assigned to `base_salary`
- Monitor pagination is offset-style in the POST body (`"location": "body"`)

## Config
```json
{
  "monitor_config": {
    "api_url": "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts",
    "method": "POST",
    "json_path": "data.job_post_list",
    "post_data": "{\"keyword\":\"\",\"limit\":20,\"offset\":0}",
    "browser": true,
    "pagination": {"param_name": "offset", "style": "offset", "increment": 20, "location": "body"},
    "fields": {
      "title": "title",
      "description": ["=<h3>Responsibilities</h3>", "description", "=<h3>Qualifications</h3>", "requirement"],
      "locations": {"concat": ["city_info.en_name", "city_info.parent.parent.en_name"], "separator": ", "}
    }
  },
  "scraper_config": {
    "enrich": ["description"],
    "api_url": "https://api.lifeattiktok.com/api/v1/public/supplier/job/posts/detail",
    "method": "POST",
    "post_body": "{\"job_post_id\": \"{id}\"}",
    "json_path": "data.job_post_detail",
    "fields": {
      "description": ["=<h3>Responsibilities</h3>", "description", "=<h3>Qualifications</h3>", "requirement"],
      "base_salary": "job_post_info.job_post_object_value_list[0].field_value"
    }
  }
}
```

## Lesson
When a list API splits content across fields, use a list spec with `=` constant headers
to concatenate them with section titles. For detail APIs, `{id}` in `post_body` or `api_url`
is replaced with the job ID extracted from the URL. `enrich` limits scraping to only the
fields that need enrichment. For locations from nested objects (city → state → country),
use `concat` with `separator: ", "` to produce unambiguous location strings.

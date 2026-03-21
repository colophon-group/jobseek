---
type: case-study
company: netflix
monitor: api_sniffer
scraper: api_sniffer
summary: "Detail API enrichment for descriptions with nested custom metadata fields"
tags: [api_sniffer, enrich, detail-api, metadata, nested-path, offset-pagination]
---
# Netflix — Detail API enrichment with nested metadata

## Setup
- Monitor: api_sniffer (GET with offset pagination)
- Scraper: api_sniffer (enriches description from detail API)

## Key decisions
- List API returns titles, locations, and metadata but lacks full job descriptions
- Used `enrich: ["description"]` to fetch descriptions from the detail API per job
- Custom `User-Agent` and `Referer` headers required — API rejects requests without them
- Nested metadata extraction: `custom_JD.data_fields.work_type` reaches deep into the
  response to extract work type from a custom data structure
- `metadata.*` prefix used for fields that go into the extras bag (team, business_unit, job_id, work_type)
- Pagination uses offset-style (`start` param, increment 10)

## Config
```json
{
  "monitor_config": {
    "api_url": "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com&start=0&num=10&sort_by=relevance",
    "method": "GET",
    "json_path": "positions",
    "url_field": "canonicalPositionUrl",
    "request_headers": {
      "User-Agent": "Mozilla/5.0 ...",
      "Referer": "https://explore.jobs.netflix.net/careers"
    },
    "pagination": {
      "param_name": "start",
      "style": "offset",
      "start_value": 0,
      "increment": 10,
      "location": "query"
    },
    "fields": {
      "title": "name",
      "locations": "locations",
      "job_location_type": "work_location_option",
      "metadata.department": "department"
    }
  },
  "scraper_config": {
    "enrich": ["description"],
    "fields": {
      "description": "job_description",
      "date_posted": "t_create",
      "metadata.work_type": "custom_JD.data_fields.work_type"
    }
  }
}
```

## Lesson
Use `enrich` when the list API is missing specific fields (like descriptions) but the
detail API has them. This avoids fetching full detail pages for every job — only the
specified fields are enriched. For deeply nested custom fields, use dot-notation paths
like `custom_JD.data_fields.work_type`.

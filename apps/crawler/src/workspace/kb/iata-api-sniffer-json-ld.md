---
type: case-study
company: iata
monitor: api_sniffer
scraper: json-ld
summary: "Cornerstone OnDemand POST API with body pagination and bearer token auth"
tags: [cornerstone, csod, post-body, bearer-token, body-pagination, auth-headers]
---
# IATA — Cornerstone OnDemand (CSOD) POST API with body pagination

## Setup
- Monitor: api_sniffer (POST with pagination in request body, browser mode)
- Scraper: json-ld (static, no rendering needed for detail pages)

## Key decisions
- CSOD career sites use a POST-based search API at `eu-fra.api.csod.com/rec-job-search/external/jobs`
- Pagination is embedded in the POST body (`pageNumber` field), not in query parameters —
  requires `pagination.location: "body"` config
- API requires a bearer token in the `authorization` header — token is captured from
  the browser session during api_sniffer detection
- POST body includes many required fields (`careerSiteId`, `cultureId`, etc.) that must
  match the specific CSOD tenant
- `browser: true` is needed because the token must be captured from a live session
- Rich data from the API (title, description, locations, date_posted) means scraper only
  needs simple json-ld for any additional enrichment

## Config
```json
{
  "monitor_config": {
    "api_url": "https://eu-fra.api.csod.com/rec-job-search/external/jobs",
    "method": "POST",
    "json_path": "data.requisitions",
    "browser": true,
    "post_data": "{\"careerSiteId\":1,\"careerSitePageId\":1,\"pageNumber\":1,\"pageSize\":25,...}",
    "pagination": {
      "param_name": "pageNumber",
      "style": "page",
      "start_value": 1,
      "increment": 1,
      "location": "body"
    },
    "request_headers": {
      "authorization": "Bearer <jwt-token>",
      "content-type": "application/json"
    },
    "fields": {
      "title": "displayJobTitle",
      "description": "externalDescription",
      "locations": "locations[].city",
      "date_posted": "postingEffectiveDate"
    }
  }
}
```

## Lesson
Cornerstone OnDemand (CSOD) sites use POST-based search APIs with body pagination.
Key things to remember: (1) set `pagination.location: "body"` for POST body pagination,
(2) `browser: true` is required to capture the auth token, (3) the POST body has many
tenant-specific fields that must be preserved exactly as captured. This pattern applies
to other CSOD tenants — look for `csod.com` in the API URL.

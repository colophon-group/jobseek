---
type: case-study
company: bcforward
monitor: api_sniffer
scraper: skip
summary: "Replace unlinked decade-old inventory tenant with official Sprockets source; direct HTTP GraphQL with proven nested-body pagination, cross-checked against 57 tenant-specific sitemap detail URLs; retain conflicting wage/employment values only as raw metadata."
tags: ['sprockets', 'graphql', 'pagination', 'legacy-board']
---
# Bcforward — Replace unlinked decade-old inventory tenant with official Sprockets source; direct HTTP GraphQL with proven nested-body pagination, cross-checked against 57 tenant-specific sitemap detail URLs; retain conflicting wage/employment values only as raw metadata.

## Setup
- Monitor: api_sniffer
- Scraper: skip

## Key decisions
- The inventory SmartRecruiters tenant belonged to BCforward and returned 404 postings, but all ten sampled posting dates were 2016–2017. The official careers route now links Sprockets. Excluded that legacy source rather than treating accessible records as current vacancies.
- Sprockets API discovery initially returned no usable jobs because the inferred field mappings omitted nested titles and locations. Direct HTTP GraphQL replay with explicit mappings returned all 57 postings in 1.5 seconds.
- Verified nested-body page pagination by temporarily using 10 items per page: six pages returned all 57 postings. Final page size is 100, with the advertised total retained for completeness checks.
- An independent sitemap monitor, filtered to this tenant's UUID detail URLs, returned exactly 57 URLs in 17.6 seconds. Static JSON-LD extracted all required fields on ten samples, but needed separate detail requests and published misleading FULL_TIME values.
- Inspected full extracted HTML for role-specific responsibilities and qualifications. Some descriptions name additional cities beyond the structured primary location; these remain in the description.
- FULL_TIME conflicts with explicit contract durations, so only explicit contractor values are normalized. Raw employment and wage fields stay in metadata: some provider salary intervals and amounts conflict with prose. Do not promote these values blindly.
- No browser or proxy is needed. Internal company careers explicitly had no openings; global research found no additional confirmed regional board.

## Config
Board: `https://jobs.sprockets.ai/en-US/bcforward/jobs`. Scraper: `skip` (rich monitor).

```json
{
  "api_url": "https://production-api.sprockets.ai/graphql",
  "method": "POST",
  "json_path": "data.jobBoardPostingPaginated.nodes",
  "total_path": "data.jobBoardPostingPaginated.totalCount",
  "browser": false,
  "post_data": {
    "operationName": "GetJobsForBoard",
    "variables": {"identifier": "bcforward", "page": 1, "itemsPer": 100},
    "query": "query GetJobsForBoard($identifier: String!, $page: Int, $itemsPer: Int) { jobBoardPostingPaginated(identifier: $identifier, page: $page, itemsPer: $itemsPer) { totalCount nodes { id jobDescription accountSubtitle wageMin wageMax employmentType workplaceType wageInterval currency postedAt group { id name location { id name address { city fullAddress state latitude longitude } } } } } }"
  },
  "pagination": {"param_name": "variables.page", "style": "page", "start_value": 1, "increment": 1, "location": "body"},
  "request_headers": {"sprockets-app": "job-board-app", "content-type": "application/json", "referer": "https://jobs.sprockets.ai/"},
  "url_template": "https://jobs.sprockets.ai/en-US/bcforward/jobs/{id}",
  "fields": {
    "title": "group.name",
    "description": "jobDescription",
    "locations": "group.location.name",
    "employment_type": {"path": "employmentType", "map": {"CONTRACTOR": "contract", "CONTRACT": "contract", "contractor": "contract", "contract": "contract"}},
    "date_posted": "postedAt",
    "job_location_type": {"path": "workplaceType", "map": {"OnSite": "onsite", "Onsite": "onsite", "ONSITE": "onsite", "ON_SITE": "onsite", "on_site": "onsite", "onsite": "onsite", "Remote": "remote", "REMOTE": "remote", "remote": "remote", "Hybrid": "hybrid", "HYBRID": "hybrid", "hybrid": "hybrid"}},
    "metadata.workplace_type": "workplaceType",
    "metadata.subtitle": "accountSubtitle",
    "metadata.wage_min": "wageMin",
    "metadata.wage_max": "wageMax",
    "metadata.wage_interval": "wageInterval",
    "metadata.currency": "currency",
    "metadata.employment_type": "employmentType"
  }
}
```

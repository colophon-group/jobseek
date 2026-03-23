---
type: case-study
company: bytedance
monitor: api_sniffer
scraper: skip
summary: "10k API cap bypass via job_category_id faceted splitting into 28 sub-boards"
tags: [api_sniffer, faceting, cap, large-company, splitting]
---
# ByteDance — Faceted splitting to bypass 10k API result cap

## Setup
- Monitor: api_sniffer (multiple instances with category-faceted queries)
- Scraper: skip (monitor provides all data via rich `fields` mapping)
- 28 total boards: 25 faceted sub-boards on careers-cn + 3 standalone instances (careers, careers-campus, careers-game)

## Key decisions
- ByteDance's careers API caps responses at 10,000 results — initial setup detected ~10k jobs
  on careers-cn but the real count was ~19,379
- Queried the API for `job_category_id_list` facets and found 9 top-level categories
- R&D (the largest category at ~8,500 jobs) was further split into 15 sub-specialties
  using sub-category IDs to keep each sub-board well under the 10k cap
- Total of 25 faceted sub-boards on careers-cn (8 non-R&D categories + 15 R&D sub-specialties
  + 2 overflow catchalls)
- Splitting by category rather than region because categories produce more evenly distributed
  counts — regional splits would leave Beijing with ~12k jobs alone
- International boards (careers, careers-campus) are separate api_sniffer instances with
  different base URLs and no faceting needed (each under 3k jobs)

## Config
```json
{
  "monitor_config": {
    "api_url": "https://jobs.bytedance.com/api/v1/search/job/posts?limit=100&offset=0&job_category_id_list=[\"7001\"]",
    "method": "GET",
    "json_path": "data.job_post_list",
    "pagination": {"param_name": "offset", "style": "offset", "increment": 100},
    "fields": {
      "title": "title",
      "description": ["=<h3>Responsibilities</h3>", "description", "=<h3>Requirements</h3>", "requirement"],
      "locations": {"concat": ["city_info.name", "city_info.parent.name"], "separator": ", "},
      "date_posted": "create_time"
    }
  }
}
```
Example board aliases: `bytedance-cn-rd-backend`, `bytedance-cn-rd-frontend`,
`bytedance-cn-product`, `bytedance-cn-design`, etc. Each varies only in the
`job_category_id_list` query parameter value.

## Lesson
When an API caps results at a fixed limit (commonly 10k), use faceted splitting: query
the API for available facet values (category, department, function), then create one board
per facet value. Choose the facet dimension that produces the most even distribution of
job counts. Always verify that no single sub-board exceeds the cap, and leave headroom
for growth. This approach scales better than region-based splitting when one region
dominates the job count.

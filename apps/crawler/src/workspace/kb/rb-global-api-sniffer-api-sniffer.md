---
type: case-study
company: rb-global
monitor: api_sniffer
scraper: api_sniffer
summary: "BigIron SaaShr portal uses page numbers in offset, exposes 49 jobs with truncated list summaries, and requires direct detail API enrichment combining job_preview, job_description and job_requirement. Generic DOM captures navigation only; rich API replay plus description and metadata enrichment provides complete jobs."
tags: ['saashr', 'pagination', 'detail-enrichment', 'multi-board']
---
# RB Global — BigIron SaaShr API replay

## Setup
- Monitor: api_sniffer
- Scraper: api_sniffer

## Key decisions
- The Oracle inventory hostname identifies RB Global, not a company named after the tenant. Official RB Global brand and careers pages establish the identity and route most worldwide brands to the central Oracle board; BigIron has a separate official SaaShr portal.
- SaaShr listing responses contain `_paging.total`. The `offset` query parameter is a page number: with `size=20`, pages 1, 2 and 3 returned 20, 20 and 9 rows. A larger single-page fetch independently returned all 49 requisitions. Do not infer offset semantics from the parameter name.
- Default rendered DOM discovery returned only navigation URLs. A strict `ShowJob` anchor selector returned zero despite the valid API inventory, so DOM zero is not empty-board evidence.
- Listing `job_description` values end in an ellipsis. The per-requisition detail endpoint supplies full `job_preview`, `job_description` and `job_requirement` HTML. Combine all three sections, with a requirements heading; preserve HTML entities rather than treating list previews as complete descriptions.
- Public `ShowJob` URLs were verified by scraper probing to load the matching detail. Detail JSON is the root object, so the generic scraper uses `json_path: "@"`.
- Only enrich `description` and `metadata` for this board. The monitor already supplies city and employment type; unsupported enrich names such as `skills`, `valid_through` and `qualifications` fail CSV validation. Requirements remain in the complete description.
- Some records have a false remote flag despite a remote address, and territory prose/custom fields can disagree with canonical location fields. Map true to remote without mapping false to onsite. Keep the canonical city and state/country metadata rather than substituting inconsistent recruiting-territory text.
- Verification found 49 distinct BigIron jobs and 440 Oracle jobs, with no shared URLs, titles or identities. Final BigIron detail tests covered Equipment Sales Representative, Sales Manager and Auction Operations Manager: all required fields and employment types passed. Both final configurations use direct HTTP; no browser resource policy is needed.

## Config
Monitor configuration:

```json
{
  "api_url": "https://secure6.saashr.com/ta/rest/ui/recruitment/companies/%7C6191308/job-requisitions",
  "method": "GET",
  "json_path": "job_requisitions",
  "total_path": "_paging.total",
  "browser": false,
  "params": {
    "size": "20",
    "sort": "desc",
    "ein_id": "102395341",
    "lang": "en-US",
    "career_portal_id": "8945670"
  },
  "pagination": {
    "param_name": "offset",
    "style": "page",
    "start_value": 1,
    "increment": 1,
    "location": "query"
  },
  "url_template": "https://secure6.saashr.com/ta/6191308.careers?ShowJob={id}&career_portal_id=8945670&ein_id=102395341",
  "fields": {
    "title": "job_title",
    "locations": "location.city",
    "employment_type": {
      "path": "employee_type.name",
      "map": {
        "Full-Time": "full_time",
        "Part-Time": "part_time"
      }
    },
    "job_location_type": {
      "path": "is_remote_job",
      "map": {
        "True": "remote"
      }
    },
    "metadata.requisition_id": "id",
    "metadata.job_categories": "job_categories",
    "metadata.base_pay_frequency": "base_pay_frequency",
    "metadata.state": "location.state",
    "metadata.country": "location.country"
  },
  "enrich": [
    "description",
    "metadata"
  ]
}
```

Scraper configuration:

```json
{
  "api_url": "https://secure6.saashr.com/ta/rest/ui/recruitment/companies/%7C6191308/job-requisitions/{job_id}?ein_id=102395341&lang=en-US&career_portal_id=8945670",
  "url_pattern": "[?&]ShowJob=(?P<job_id>[0-9]+)",
  "json_path": "@",
  "fields": {
    "title": "job_title",
    "description": [
      "job_preview",
      "job_description",
      "=<h3>Requirements</h3>",
      "job_requirement"
    ],
    "locations": "location.city",
    "employment_type": {
      "path": "employee_type.name",
      "map": {
        "Full-Time": "full_time",
        "Part-Time": "part_time"
      }
    },
    "job_location_type": {
      "path": "is_remote_job",
      "map": {
        "True": "remote"
      }
    },
    "metadata.requisition_id": "id",
    "metadata.job_categories": "job_categories",
    "metadata.base_pay_frequency": "base_pay_frequency",
    "metadata.required_degree": "required_degree",
    "metadata.min_experience": "min_experience",
    "metadata.travel_percent": "travel_percent",
    "metadata.manage_others": "manage_others",
    "metadata.state": "location.state",
    "metadata.country": "location.country"
  },
  "enrich": [
    "description",
    "metadata"
  ]
}
```

---
type: case-study
company: goldman-sachs
monitor: mixed
scraper: mixed
summary: "3 boards across Taleo, Greenhouse, api_sniffer with false-positive filtering"
tags: [taleo, greenhouse, api_sniffer, multi-ats, false-positive]
---
# Goldman Sachs — Multi-ATS setup with false-positive filtering

## Setup
- 3 boards across different ATS platforms
- Monitor types: api_sniffer (Taleo main careers), greenhouse (campus recruiting), api_sniffer (custom API)
- Scraper types: skip (rich monitors), skip (Greenhouse)

## Key decisions
- Goldman Sachs uses multiple ATS platforms for different recruiting functions: Taleo for
  experienced hire careers, Greenhouse for campus/intern recruiting, and a custom API for
  select divisions
- The Taleo board required api_sniffer with POST replay — the Taleo REST API uses a POST
  search endpoint with JSON body containing pagination and filter parameters
- During probe, a Rippling false positive was detected: slug "gs" matched a warehouse/logistics
  company using Rippling ATS. Verified by checking company industry (financial services vs.
  warehousing) and confirming the Rippling board was unrelated
- The Greenhouse campus board uses token "goldmansachscampus" — discovered via the standard
  Greenhouse boards API endpoint
- Each probe result was validated against Goldman Sachs's expected industry (financial services)
  to catch slug collisions — this is critical for short slugs that match multiple companies

## Board breakdown
| Board alias        | Monitor       | Scraper | Notes                          |
|--------------------|---------------|---------|--------------------------------|
| gs-careers         | api_sniffer   | skip    | Taleo POST API, main careers   |
| gs-campus          | greenhouse    | skip    | Token: goldmansachscampus      |
| gs-engineering     | api_sniffer   | skip    | Custom internal API             |

## Config
```json
{
  "monitor_config (gs-careers)": {
    "api_url": "https://goldmansachs.taleo.net/careersection/rest/jobboard/searchjobs",
    "method": "POST",
    "post_data": "{\"multilineEnabled\":false,\"sortingSelection\":{\"sortBySelectionParam\":\"5\",\"ascendingSortingOrder\":\"false\"},\"fieldData\":{\"fields\":{},\"currentRecordCount\":0,\"currentPageNo\":1,\"pageSize\":100}}",
    "json_path": "requisitionList",
    "pagination": {"param_name": "currentPageNo", "style": "page", "location": "body"},
    "fields": {
      "title": "column[0].value",
      "locations": "column[2].value",
      "date_posted": "column[3].value"
    }
  },
  "monitor_config (gs-campus)": {
    "token": "goldmansachscampus"
  }
}
```

## Lesson
Short company slugs (2-3 characters) are especially prone to ATS probe false positives —
another company may coincidentally use the same slug on a different platform. Always verify
probe hits against the expected company industry, website, and name. For Taleo boards, the
api_sniffer with POST replay is the standard approach: capture the search endpoint's POST
body and configure body-based pagination. Multi-ATS companies often segment by recruiting
function (experienced/campus/executive) rather than by region.

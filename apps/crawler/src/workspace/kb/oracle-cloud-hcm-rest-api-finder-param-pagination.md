# Oracle Cloud HCM — REST API with finder-param pagination

## Symptom

The company's careers page is hosted on Oracle Cloud HCM (Oracle Fusion) at
a URL like `*.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_N/requisitions`.
The page is a JavaScript SPA with no JSON-LD. Standard monitors (sitemap,
dom, greenhouse, workday, etc.) don't detect jobs. The `api_sniffer` probe
may find the REST API but pagination fails — every page returns the same
25 items regardless of `offset` or `limit` query params.

## Root cause

Oracle Cloud HCM exposes a REST API at:
```
https://{tenant}.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions
```

The API uses a `finder` query parameter with **semicolon-and-comma-delimited
key=value pairs** for all search/pagination parameters:
```
finder=findReqs;siteNumber=CX_1,limit=25,offset=0,sortBy=POSTING_DATES_DESC
```

Standard query params (`?offset=25`, `?limit=100`) are **silently ignored**.
The `limit` and `offset` must be inside the `finder` value.

## Solution

### 1. Discover available CX sites

Oracle HCM tenants often have multiple career sites (`CX_1`, `CX_2`, ...):

```
GET /hcmRestApi/resources/latest/recruitingCEJobRequisitions
    ?onlyData=true&finder=findReqs;siteNumber=CX_N,limit=1
```

Iterate `CX_1` through `CX_9` and check `TotalJobsCount` in the response.
Higher-numbered sites (e.g. `CX_4`) are often combined views that include
all regions.

### 2. Fetch all jobs in one request

Set `limit=200` (or higher) inside the finder param to fetch all jobs at
once without pagination:

```
GET /hcmRestApi/resources/latest/recruitingCEJobRequisitions
    ?onlyData=true
    &expand=requisitionList.workLocation,requisitionList.secondaryLocations
    &finder=findReqs;siteNumber=CX_4,facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS,limit=200,sortBy=POSTING_DATES_DESC
```

Response structure:
```json
{
  "items": [{
    "TotalJobsCount": 109,
    "requisitionList": [
      {"Id": "2723", "Title": "...", "PrimaryLocation": "Bern, Switzerland", "PostedDate": "2026-03-24", ...},
      ...
    ]
  }]
}
```

### 3. Configure api_sniffer

```json
{
  "api_url": "https://{tenant}.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.workLocation,requisitionList.secondaryLocations&finder=findReqs;siteNumber=CX_4,...,limit=200,sortBy=POSTING_DATES_DESC",
  "json_path": "items[0].requisitionList",
  "url_template": "https://{tenant}.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_4/job/{Id}",
  "fields": {
    "title": "Title",
    "locations": "PrimaryLocation",
    "date_posted": "PostedDate"
  }
}
```

No scraper needed — set `scraper_type: skip`. The API provides title,
location, and date but **not descriptions** (those require rendering the
SPA job detail page, which is expensive and fragile).

### 4. If pagination IS needed (>200 jobs)

Use `pagination.location: "suffix"` which appends `,offset=N` to the
raw API URL:

```json
{
  "pagination": {
    "param_name": "offset",
    "start": 0,
    "increment": 25,
    "location": "suffix"
  }
}
```

## When to suspect this pattern

- Careers URL contains `oraclecloud.com/hcmUI/CandidateExperience`
- Page is a React/JS SPA that shows jobs but has no JSON-LD
- `ws probe` detects nothing (no known ATS, no sitemap)
- Network tab shows requests to `/hcmRestApi/resources/latest/`
- The `finder` query param contains semicolons and commas

## Known tenants

| Tenant | Companies |
|--------|-----------|
| `iaaras` | ELCA Group |

Add new tenants as they're discovered.

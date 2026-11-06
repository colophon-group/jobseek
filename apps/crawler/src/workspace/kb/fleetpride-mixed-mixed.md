---
type: case-study
company: fleetpride
monitor: mixed
scraper: mixed
summary: "FleetPride global setup verified390 Workday jobs via all_sites=false after default aggregation lost352; official TruckPro and Crane Carrier Canada portals share ADP RM endpoint with refreshed public context, returning0 and10 jobs respectively. Cold HTTP handles session-header renewal without browser or stored token; offset pagination independently verified. Canadian role HTML, normalized employment types, dates and nine city/province addresses preserved, with truthful country-only fallback for one upstream missing address. Wheelco redirects to Workday and Betterteam technician subset excluded."
tags: ['workday', 'adp', 'multi-board', 'header-refresh', 'canada', 'verified-empty']
---
# Fleetpride — FleetPride global setup verified390 Workday jobs via all_sites=false after default aggregation lost352; official TruckPro and Crane Carrier Canada portals share ADP RM endpoint with refreshed public context, returning0 and10 jobs respectively. Cold HTTP handles session-header renewal without browser or stored token; offset pagination independently verified. Canadian role HTML, normalized employment types, dates and nine city/province addresses preserved, with truthful country-only fallback for one upstream missing address. Wheelco redirects to Workday and Betterteam technician subset excluded.

## Setup
- Monitor: mixed
- Scraper: mixed

## Key decisions
- Verified on 2026-11-06: 390 FleetPride Workday jobs, 10 Crane Carrier Canada ADP jobs, and zero TruckPro ADP jobs. All three portals are linked from official employer careers pages.
- Default Workday all-sites aggregation returned 38 while its probe reported 390. Only one official tenant site was confirmed; `all_sites:false` restored all 390. Do not restrict tenants with additional distinct sites without checking their scope.
- Early ADP probes mistook eight career-site configuration links for jobs, and rendered DOM returned zero without a verified empty state. A later Canadian probe captured the actual `jobRequisitions` endpoint.
- Existing `api_sniffer` HTTP replay supports `request_header_refresh`; native `adp` covers the different WorkforceNow API. Fresh public context avoids a browser and stored session tokens. Independent cold HTTP calls verified both portals; five Canadian pages with `$top=2` and `$skip=0,2,4,6,8` returned distinct rows and a stable count of 10.
- Explicit `$select` supplies public titles, full HTML descriptions, dates and employment types. Without it, the source returned internal title codes and omitted descriptions.
- Normalize observed enums `FTH - Hourly Full-Time` and `FTS - Salary Full-Time` to `full_time`.
- Nine Canadian jobs expose city/province/country addresses. Requisition `5001220905306` has no location in either list/detail API or description. Only country-level Canada fallback is justified by this exclusive Canadian employer portal; no city is inferred.
- Wheelco redirects to FleetPride. Betterteam is overlapping technician recruitment and points applicants to FleetPride careers for all jobs; it was excluded.
- `ws compare-boards` found no overlap in stored samples. Workday artifacts cap URLs at 100, so that comparison alone is not a full inventory identity proof.

## Config
Workday monitor with native `workday` scraper:

```json
{"company":"fleetpride","wd_instance":"wd1","site":"fleetpridecareer","all_sites":false}
```

Canadian rich `api_sniffer` monitor with scraper `skip`:

```json
{
  "api_url":"https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/apply-custom-filters",
  "method":"GET",
  "json_path":"jobRequisitions",
  "total_path":"count",
  "params":{
    "$orderby":"postingDate desc",
    "$select":"reqId,jobTitle,publishedJobTitle,type,jobDescription,jobQualifications,workLocations,workLevelCode,clientRequisitionID,postingDate,requisitionLocations",
    "$top":"10"
  },
  "request_headers":{"rolecode":"manager","Accept-Language":"en-US","Accept":"application/json"},
  "request_header_refresh":{
    "source_url":"https://myjobs.adp.com/public/staffing/v1/career-site/cranecarriercanadacareers",
    "fields":{"myJobsToken":"myJobsToken"}
  },
  "url_template":"https://myjobs.adp.com/cranecarriercanadacareers/cx/job-details?reqId={reqId}",
  "pagination":{"param_name":"$skip","style":"offset","start_value":0,"increment":10,"location":"query"},
  "fields":{
    "title":"jobTitle",
    "description":"jobDescription",
    "locations":"requisitionLocations[].join(', ', [address.cityName, address.countrySubdivisionLevel1.longName, address.country.longName][?@]) || `[\"Canada\"]`",
    "employment_type":{"path":"workLevelCode","map":{"FTH - Hourly Full-Time":"full_time","FTS - Salary Full-Time":"full_time"}},
    "date_posted":"postingDate",
    "metadata.reqId":"reqId"
  }
}
```

TruckPro uses the same endpoint with `truckprocareers` in the public-context URL and posting URL template, and no Canadian location fallback. Its `count=0, jobRequisitions=[]` was independently verified before recording verified-empty feedback.

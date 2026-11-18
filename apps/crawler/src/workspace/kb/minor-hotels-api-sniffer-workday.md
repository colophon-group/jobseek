---
type: case-study
company: minor-hotels
monitor: api_sniffer
scraper: workday
summary: "Minor Hotels requires three regional boards; the shared Workday tenant must be restricted by Business_Unit in POST data because the generic monitor ignores the careers URL query filter."
tags: ['workday', 'facet-filter', 'multiregion', 'smartrecruiters']
---
# Minor-Hotels — Minor Hotels requires three regional boards; the shared Workday tenant must be restricted by Business_Unit in POST data because the generic monitor ignores the careers URL query filter.

## Setup
- Monitor: api_sniffer
- Scraper: workday

## Key decisions
- The official Minor Hotels careers page points to three independent regional portals: Workday for Asia, Africa and the Middle East; SuccessFactors for Europe and the Americas; SmartRecruiters for Australia and New Zealand. Their tested inventories contained 861, 850 and 168 jobs respectively, with no URL overlap.
- The Workday tenant belongs to Minor International and also serves Minor Food and Lifestyle. The visible Minor Hotels careers link carries a `Business_Unit` query parameter, but the generic Workday monitor does not apply that filter. Replay the Workday jobs POST endpoint with `appliedFacets.Business_Unit` to keep the hotel division in scope.
- The Workday endpoint rejects page limits above 20 with HTTP 400. Use offset pagination in steps of 20. The filtered API returned exactly the 861 jobs shown by the browser, and the Workday detail scraper returned a clean description and location.
- The SmartRecruiters tenant includes 14 jobs outside Australia and New Zealand. Use an item filter on `location.country` to retain the 168 AU/NZ hotel jobs. The detail API supplies descriptions.
- The SuccessFactors `googlefeed.xml` must use the `successfactors` RSS preset. The generic RSS parser found all 850 jobs but omitted locations; the preset populated locations for all 850.

## Config
Workday monitor (board `https://minor.wd102.myworkdayjobs.com/Careers?Business_Unit=aad9f66d5e7f1018d61630cb61990000`):

```json
{"api_url":"https://minor.wd102.myworkdayjobs.com/wday/cxs/minor/Careers/jobs","method":"POST","json_path":"jobPostings","url_template":"https://minor.wd102.myworkdayjobs.com/Careers{externalPath}","post_data":{"appliedFacets":{"Business_Unit":["aad9f66d5e7f1018d61630cb61990000"]},"limit":20,"offset":0,"searchText":""},"pagination":{"param_name":"offset","style":"offset","start_value":0,"increment":20,"location":"body"}}
```

Scraper: `workday` with no additional configuration.

Europe and Americas monitor: `{"preset":"successfactors","feed_url":"https://euamcareers.minorhotels.com/googlefeed.xml"}`. The rich feed needs no scraper.

Australia and New Zealand monitor:

```json
{"api_url":"https://api.smartrecruiters.com/v1/companies/MinorInternational/postings","method":"GET","json_path":"content","url_template":"https://jobs.smartrecruiters.com/MinorInternational/{id}","params":{"limit":100},"pagination":{"param_name":"offset","style":"offset","start_value":0,"increment":100,"location":"query"},"item_filter":{"include":{"location.country":["au","nz"]}}}
```

Scraper: `smartrecruiters` with no additional configuration.

---
step: select_monitor
symptom: "Eightfold AI API enforces 2000 offset cap — jobs beyond offset 2000 are unreachable"
tags: [eightfold, api, offset-cap, pagination]
---
# Eightfold AI API enforces 2000 offset cap

## Problem
Eightfold AI (citi.eightfold.ai, ms.eightfold.ai, etc.) returns paginated
results but enforces a hard 2000-result offset limit. When a company has more
than 2000 jobs, the remainder is silently dropped — the API simply returns
empty results for offset values above 2000. No query faceting workaround
exists within the Eightfold API itself.

## Solution
Accept partial coverage if no alternative board exists. Before settling for
the truncated result set, check whether the company also has a sitemap-based
board (e.g., on a subdomain like `jobs.company.com` or a `/careers/sitemap.xml`
path) that covers the full listing set. If found, use `sitemap` + `json_ld`
as the primary monitor/scraper and keep the Eightfold board as a secondary
source only if it adds unique listings not present in the sitemap.

```bash
# Check for sitemap alternatives
ws probe monitor -n <expected-count>
# If sitemap found with full coverage, prefer it
ws select monitor sitemap --config '{"sitemap_url": "https://jobs.company.com/sitemap.xml"}'
```

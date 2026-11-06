---
step: configure
symptom: Official Enercon SaaSHR careers URL appears empty in DOM while generic root navigation finds login-help links; API list descriptions are truncated previews.
tags: ['saashr', 'ukg', 'api_sniffer', 'empty-query', 'description-enrichment']
---
# Official Enercon SaaSHR careers URL appears empty in DOM while generic root navigation finds login-help links; API list descriptions are truncated previews.

## Problem
Official Enercon SaaSHR careers URL appears empty in DOM while generic root navigation finds login-help links; API list descriptions are truncated previews.

## Solution
Preserve the exact official CareersSearch empty query flag while probing the page. For the verified tenant, API capture found /ta/rest/ui/recruitment/companies/%7C6194038/job-requisitions with job_requisitions array and authoritative _paging.total. Direct HTTP replay with offset1,size100 returned all25 jobs without a browser. Use actual source IDs in official ShowJob URLs, and enrich descriptions from same API path /{id}, combining full job_description and job_requirement. Never accept truncated list previews or login/language links as jobs. Verify current pagination and totals per tenant; false is_remote_job does not prove onsite, and pay amounts without explicit currency must not be assigned an invented currency.

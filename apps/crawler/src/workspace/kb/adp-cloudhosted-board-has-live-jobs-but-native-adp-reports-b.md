---
step: configure
symptom: ADP cloud-hosted board has live jobs but native ADP reports board no longer exists; generic probe chooses nested requisitionLocations instead of jobs.
tags: ['adp', 'cloud', 'api_sniffer', 'pagination', 'detail-api']
---
# ADP cloud-hosted board has live jobs but native ADP reports board no longer exists; generic probe chooses nested requisitionLocations instead of jobs.

## Problem
ADP cloud-hosted board has live jobs but native ADP reports board no longer exists; generic probe chooses nested requisitionLocations instead of jobs.

## Solution
Verify the official linked workforcenow.cloud.adp.com source. Replay its public staffing/v1/job-requisitions API over HTTP with json_path jobRequisitions and total_path meta.totalNumber. Use external public IDs from customFieldGroup.stringFields whose nameCode is ExternalJobID, not clientRequisitionID or internal itemID. Verify array positioning before mapping. ADP pagination is one-based: this source returned ten rows at =1,=10 and four at =11, matching total14. Fetch full detail at job-requisitions/{externalId} on the same official cloud host; detail is a direct root object with requisitionDescription, not jobRequisitions[0]. Inspect source and emitted IDs before accepting; do not trust generic probe path or assume offset zero.

---
step: configure
symptom: ADP myjobs.adp.com portals can produce zero rendered links and api_sniffer may mistake the career-site links array for job inventory; WorkforceNow adp monitor does not cover this portal.
tags: ['adp', 'api-sniffer', 'header-refresh', 'pagination', 'false-positive']
---
# ADP myjobs.adp.com portals can produce zero rendered links and api_sniffer may mistake the career-site links array for job inventory; WorkforceNow adp monitor does not cover this portal.

## Problem
ADP myjobs.adp.com portals can produce zero rendered links and api_sniffer may mistake the career-site links array for job inventory; WorkforceNow adp monitor does not cover this portal.

## Solution
Confirm official portal ownership, then capture the actual my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/apply-custom-filters response. Existing api_sniffer supports cold HTTP replay with request_header_refresh: source_url is the same-origin myjobs.adp.com/public/staffing/v1/career-site/<portal>, fields maps myJobsToken to myJobsToken, and rolecode=manager is a fixed request header. Never store the captured token. Map jobRequisitions, count, reqId URL template, HTML jobDescription and requisitionLocations; select postingDate/workLevelCode explicitly and normalize observed enums. Verify offset pagination against count. For FleetPride Canada this yielded10 jobs; TruckPro yielded count0 and jobRequisitions=[] with fresh public context. Missing address in one Canadian listing was also missing in detail and description, so only verified country-level fallback was used.

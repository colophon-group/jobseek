---
step: monitor
symptom: HeavenHR probe suggests a marketing sitemap, incomplete rich fields and nonfunctional /jobs/{id} detail URLs for an official employer board.
tags: ['heavenhr', 'api_sniffer', 'pagination', 'canonical-url', 'location']
---
# HeavenHR probe suggests a marketing sitemap, incomplete rich fields and nonfunctional /jobs/{id} detail URLs for an official employer board.

## Problem
HeavenHR probe suggests a marketing sitemap, incomplete rich fields and nonfunctional /jobs/{id} detail URLs for an official employer board.

## Solution
On the verified Tremco CPG HeavenHR board, the sitemap was the provider marketing sitemap (58 non-job URLs), so reject it. The public /api/v1/positions/public/vacancies/ endpoint returned 20 jobs with companyId and zero-based page=0 pagination; start_value=0 was necessary to retain page 1 with small page sizes. Compare API IDs with rendered employer listing links. Official details used /jobs/{id}/apply?language=de, not /jobs/{id}. Direct HTTP detail extraction succeeded at https://api.heavenhr.com/api/v1/positions/public/vacancies/{job_id}/ with URL capture /jobs/(?P<job_id>[^/]+)/apply and json_path data[0]. Summary descriptions were empty; detail descriptions were complete. Location labels contained employer names, so verify geography from every affected description before mapping; one illbruck label meant Austria, not Germany. Preserve mixed schedules and trainee types as source metadata. Re-verify endpoint shape, IDs, geography and completeness for each new employer; these observations are not a universal preset.

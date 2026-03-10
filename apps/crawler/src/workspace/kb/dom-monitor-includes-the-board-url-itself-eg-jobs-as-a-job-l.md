---
step: select_monitor
symptom: DOM monitor includes the board URL itself (e.g. /jobs) as a job link alongside real job detail URLs (e.g. /jobs/slug)
tags: ['dom', 'url_filter', 'false-positive']
---
# DOM monitor includes the board URL itself (e.g. /jobs) as a job link alongside real job detail URLs (e.g. /jobs/slug)

## Problem
DOM monitor includes the board URL itself (e.g. /jobs) as a job link alongside real job detail URLs (e.g. /jobs/slug)

## Solution
Use url_filter with a pattern that requires a path segment after the board path, e.g. '/jobs/.+', to exclude the board listing page itself

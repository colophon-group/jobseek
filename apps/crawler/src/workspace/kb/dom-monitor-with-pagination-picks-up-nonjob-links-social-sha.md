---
step: select_monitor
symptom: DOM monitor with pagination picks up non-job links (social sharing, pagination, nav) on each page, inflating count and preventing natural stop
tags: ['dom', 'pagination', 'url_filter', 'inflated-count']
---
# DOM monitor with pagination picks up non-job links (social sharing, pagination, nav) on each page, inflating count and preventing natural stop

## Problem
DOM monitor with pagination picks up non-job links (social sharing, pagination, nav) on each page, inflating count and preventing natural stop

## Solution
Add url_filter matching the job detail URL pattern (e.g. /jobs/FolderDetail/) so pagination only counts new job links and stops when no new jobs are found

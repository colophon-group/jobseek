---
step: select_monitor
symptom: "DOM monitor with render: true finds far fewer jobs than expected on pages with infinite scroll/lazy loading (79 vs 1037)"
tags: ['dom', 'sitemap', 'lazy-loading', 'infinite-scroll', 'pagination']
---
# DOM monitor with render: true finds far fewer jobs than expected on pages with infinite scroll/lazy loading (79 vs 1037)

## Problem
DOM monitor with render: true finds far fewer jobs than expected on pages with infinite scroll/lazy loading (79 vs 1037)

## Solution
Use sitemap monitor with url_filter instead. Sitemap often contains all job URLs regardless of frontend pagination. Filter pattern should match job detail URL structure (e.g. '/en/jobs/r' for paths with job IDs).

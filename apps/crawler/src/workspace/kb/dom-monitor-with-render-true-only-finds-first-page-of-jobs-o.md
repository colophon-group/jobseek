---
step: select_monitor
symptom: "DOM monitor with render: true only finds first page of jobs on JS-paginated career sites (70 vs 1,411 expected)"
tags: ['dom', 'sitemap', 'pagination', 'js-rendered']
---
# DOM monitor with render: true only finds first page of jobs on JS-paginated career sites (70 vs 1,411 expected)

## Problem
DOM monitor with render: true only finds first page of jobs on JS-paginated career sites (70 vs 1,411 expected)

## Solution
Use sitemap monitor with url_filter to match job URL pattern (e.g., '/en/jobs/'). Sitemap provides full coverage without needing to handle JS pagination.

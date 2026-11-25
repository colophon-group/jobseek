---
step: select_monitor
symptom: BCforward inventory SmartRecruiters tenant returned 404 accessible postings but all sampled dates were 2016-2017; current official careers route used Sprockets.
tags: ['smartrecruiters', 'sprockets', 'legacy-board', 'api_sniffer', 'pagination', 'data-quality']
---
# BCforward inventory SmartRecruiters tenant returned 404 accessible postings but all sampled dates were 2016-2017; current official careers route used Sprockets.

## Problem
BCforward inventory SmartRecruiters tenant returned 404 accessible postings but all sampled dates were 2016-2017; current official careers route used Sprockets.

## Solution
Verify official inbound careers links and posting dates before accepting an inventory seed. For Sprockets compare tenant-filtered sitemap detail URLs with GraphQL jobBoardPostingPaginated total. Direct HTTP POST with nested variables.page pagination returned all 57 postings; force a small page size to verify continuation. Provider FULL_TIME and wage intervals can conflict with explicit contract and hourly prose: preserve raw metadata and omit ambiguous normalized classifications.

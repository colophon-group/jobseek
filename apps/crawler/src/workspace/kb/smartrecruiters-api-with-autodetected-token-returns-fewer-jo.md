---
step: select_monitor
symptom: SmartRecruiters API with auto-detected token returns fewer jobs than visible on the board (4 vs 10)
tags: ['smartrecruiters', 'dom', 'url_filter', 'subset', 'custom-domain']
---
# SmartRecruiters API with auto-detected token returns fewer jobs than visible on the board (4 vs 10)

## Problem
SmartRecruiters API with auto-detected token returns fewer jobs than visible on the board (4 vs 10)

## Solution
Fall back to dom monitor with render:true and url_filter. The custom domain (jobs.booking.com) may serve jobs from multiple sources (SmartRecruiters + iCIMS), so the SR API only covers a subset.

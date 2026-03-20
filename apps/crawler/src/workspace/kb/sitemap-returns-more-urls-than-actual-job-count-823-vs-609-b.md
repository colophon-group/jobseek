---
step: select_monitor
symptom: Sitemap returns more URLs than actual job count (823 vs 609) because it includes non-job pages (category, location, landing pages)
tags: ['sitemap', 'url_filter', 'phenom', 'overcounting']
---
# Sitemap returns more URLs than actual job count (823 vs 609) because it includes non-job pages (category, location, landing pages)

## Problem
Sitemap returns more URLs than actual job count (823 vs 609) because it includes non-job pages (category, location, landing pages)

## Solution
Add url_filter to sitemap config matching the job detail URL pattern (e.g. '/global/en/job/'). Verify filtered count matches displayed job total.

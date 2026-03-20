---
step: select_monitor
symptom: Phenom People sitemap contains non-job pages (recruitment-scam-warning, latam-listings, etc.) inflating URL count
tags: ['phenom', 'sitemap', 'url_filter', 'non-job-urls']
---
# Phenom People sitemap contains non-job pages (recruitment-scam-warning, latam-listings, etc.) inflating URL count

## Problem
Phenom People sitemap contains non-job pages (recruitment-scam-warning, latam-listings, etc.) inflating URL count

## Solution
Add url_filter '/job/' to sitemap config to keep only actual job URLs. Reduced 379 to 337 URLs, matching displayed count of 332.

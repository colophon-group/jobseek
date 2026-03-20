---
step: select_scraper
symptom: Umantis monitor finds jobs (150 URLs) but scraper probe fails with DNS errors or timeouts on recruitingapp-{ID}.umantis.com URLs
tags: ['umantis', 'redirect', 'dns', 'scraper-fail']
---
# Umantis monitor finds jobs (150 URLs) but scraper probe fails with DNS errors or timeouts on recruitingapp-{ID}.umantis.com URLs

## Problem
Umantis monitor finds jobs (150 URLs) but scraper probe fails with DNS errors or timeouts on recruitingapp-{ID}.umantis.com URLs

## Solution
The Umantis detail pages may redirect to the company's own domain. Check with curl -sI if URLs return 302 redirects. If so, use the company's custom job portal domain (e.g., jobs.company.ch) as the board URL with DOM monitor instead, and scrape from those pages (json-ld typically works).

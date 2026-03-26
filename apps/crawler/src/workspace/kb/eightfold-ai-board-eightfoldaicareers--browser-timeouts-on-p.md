---
step: select_monitor
symptom: Eightfold AI board (eightfold.ai/careers) - browser timeouts on page load, api_sniffer and dom probes fail
tags: ['eightfold', 'sitemap', 'json-ld', 'ats']
---
# Eightfold AI board (eightfold.ai/careers) - browser timeouts on page load, api_sniffer and dom probes fail

## Problem
Eightfold AI board (eightfold.ai/careers) - browser timeouts on page load, api_sniffer and dom probes fail

## Solution
Use the dedicated `eightfold` monitor type (sitemap wrapper, cost=8). It auto-detects *.eightfold.ai domains and constructs the sitemap URL. Pairs with json-ld scraper (no render needed - static HTML contains JSON-LD schema.org JobPosting markup with title, description, location, employment_type, date_posted, valid_through).

Config: `{"url_filter": "/careers/job/"}`

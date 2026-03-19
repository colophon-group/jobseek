---
step: select_monitor
symptom: Eightfold AI board (eightfold.ai/careers) - browser timeouts on page load, api_sniffer and dom probes fail
tags: ['eightfold', 'sitemap', 'json-ld', 'ats']
---
# Eightfold AI board (eightfold.ai/careers) - browser timeouts on page load, api_sniffer and dom probes fail

## Problem
Eightfold AI board (eightfold.ai/careers) - browser timeouts on page load, api_sniffer and dom probes fail

## Solution
Use sitemap monitor (sitemap.xml available at /careers/sitemap.xml) with json-ld scraper (no render needed - static HTML contains JSON-LD schema.org JobPosting markup with title, description, location, employment_type, date_posted, valid_through)

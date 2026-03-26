---
step: select_monitor
symptom: Eightfold AI (PCSX) careers portal times out on all browser-based monitors (dom, api_sniffer). Page.goto exceeds 20-30s timeout.
tags: ['eightfold', 'sitemap', 'timeout', 'json-ld']
---
# Eightfold AI (PCSX) careers portal times out on all browser-based monitors (dom, api_sniffer). Page.goto exceeds 20-30s timeout.

## Problem
Eightfold AI (PCSX) careers portal times out on all browser-based monitors (dom, api_sniffer). Page.goto exceeds 20-30s timeout.

## Solution
Use the dedicated `eightfold` monitor type — it auto-constructs the sitemap URL from the board URL and pairs with the json-ld scraper. Add url_filter=/careers/job/ to exclude non-job URLs. JSON-LD scraper works on individual job pages without render since schema.org markup is in static HTML.

Example board config: `monitor_type=eightfold`, `monitor_config={"url_filter": "/careers/job/"}`, `scraper_type=json-ld`

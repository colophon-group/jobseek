---
step: select_monitor
symptom: Phenom People careers site causes browser timeouts for api_sniffer and dom monitor probes due to heavy JS rendering
tags: ['phenom', 'timeout', 'sitemap', 'json-ld', 'browser-render']
---
# Phenom People careers site causes browser timeouts for api_sniffer and dom monitor probes due to heavy JS rendering

## Problem
Phenom People careers site causes browser timeouts for api_sniffer and dom monitor probes due to heavy JS rendering

## Solution
Use sitemap monitor + json-ld scraper instead. Phenom People sites have reliable sitemaps and JSON-LD markup on job pages, bypassing the need for browser rendering entirely.

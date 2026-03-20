---
step: select_monitor
symptom: api_sniffer pagination config (page_number param) has no effect — monitor returns same 10 items regardless of pagination settings
tags: ['api_sniffer', 'pagination', 'sitemap-fallback']
---
# api_sniffer pagination config (page_number param) has no effect — monitor returns same 10 items regardless of pagination settings

## Problem
api_sniffer pagination config (page_number param) has no effect — monitor returns same 10 items regardless of pagination settings

## Solution
api_sniffer re-sniffs the page each run and uses the captured request, ignoring pagination config merge. Fall back to sitemap monitor for full coverage when the site has a comprehensive sitemap.

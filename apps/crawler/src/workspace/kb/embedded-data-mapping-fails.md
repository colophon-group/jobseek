---
step: select_scraper
symptom: Probe detects embedded data pattern but field mapping shows 0/N
tags: [embedded, nextdata, AF_initDataCallback, field-mapping, scraper]
---
# Probe detects embedded data but field mapping fails

## Problem
Scraper probe detects a pattern (AF_initDataCallback, NextData, script tags)
but the heuristic field mapping fails, showing 0/N for required fields.
The data IS there — the auto-generated mapping is wrong.

## Solution
1. Download raw HTML (NOT WebFetch — it summarizes via LLM):
   ```bash
   curl -s <url> -o /tmp/page.html
   ```
2. Search for the detected pattern in the raw HTML
3. Write a small Python script to parse and print the data structure,
   identifying array indices and object keys for title, description, etc.
4. Configure the `embedded` scraper with correct `pattern`, `path`, and `fields`:
   ```bash
   ws help scraper embedded
   ws select scraper embedded --config '{"pattern": "...", "path": "...", "fields": {...}}'
   ws run scraper
   ```

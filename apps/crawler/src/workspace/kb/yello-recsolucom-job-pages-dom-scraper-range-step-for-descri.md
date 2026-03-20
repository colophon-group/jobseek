---
step: select_scraper
symptom: Yello (recsolu.com) job pages: DOM scraper range step for description consumes sidebar elements, making location extraction fail with forward-only cursor
tags: ['yello', 'recsolu', 'dom-scraper', 'cursor-reset', 'sidebar-extraction']
---
# Yello (recsolu.com) job pages: DOM scraper range step for description consumes sidebar elements, making location extraction fail with forward-only cursor

## Problem
Yello (recsolu.com) job pages: DOM scraper range step for description consumes sidebar elements, making location extraction fail with forward-only cursor

## Solution
Use 'from: 0' to reset cursor for sidebar fields after description range. Yello sidebar has combined divs like 'Country/Region Luxembourg' — use regex to extract value: {"text": "Country/Region", "field": "locations", "from": 0, "regex": "Country/Region\\s+(.+)"}

---
step: select_scraper
symptom: DOM scraper silently skips fields or returns empty values
tags: [dom, scraper, steps, flat-json, dom-order]
---
# DOM scraper silently skips fields

## Problem
DOM scraper steps extract some fields but silently skip others.
Fields appear as empty or missing even though they exist on the page.

## Solution
DOM scraper uses a forward-only cursor — steps must follow the order
elements appear in the DOM. Wrong order silently skips fields.

1. Inspect `flat.json` in the scraper probe artifacts to verify element order
2. Reorder steps to match the DOM sequence
3. If two fields are in the same container, extract the one that appears
   first in the DOM before the second

**Always prefer `render: false`** — only use `render: true` when static
fetch produces empty results. Check if the data exists in static HTML first.

---
step: select_scraper
symptom: DOM scraper offset from metadata field misses description when pages have variable optional fields (e.g., salary fields present on some job pages but not others)
tags: ['dom', 'scraper', 'offset', 'variable-fields', 'range-extraction']
---
# DOM scraper offset from metadata field misses description when pages have variable optional fields (e.g., salary fields present on some job pages but not others)

## Problem
DOM scraper offset from metadata field misses description when pages have variable optional fields (e.g., salary fields present on some job pages but not others)

## Solution
Use range extraction instead of fixed offset. Anchor on the last required metadata field, skip its value element, then collect with html:true and stop at a consistent footer element (e.g., 'Apply now' in the bottom links). Optional fields get included as minor noise but the full description is captured.

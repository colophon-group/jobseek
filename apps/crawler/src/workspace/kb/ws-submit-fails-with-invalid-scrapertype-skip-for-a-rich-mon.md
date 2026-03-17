---
step: submit
symptom: ws submit fails with Invalid scraper_type skip for a rich monitor board
tags: ['submit', 'validation', 'scraper', 'skip', 'rich-monitor']
---
# ws submit fails with Invalid scraper_type skip for a rich monitor board

## Problem
ws submit fails with Invalid scraper_type skip for a rich monitor board

## Solution
Validate scraper_type values against the registered scraper types so documented auto-configured scrapers like skip, workday, workable, and similar values pass CSV validation.

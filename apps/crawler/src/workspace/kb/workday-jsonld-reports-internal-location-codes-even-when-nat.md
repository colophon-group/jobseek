---
step: scraper
symptom: Workday JSON-LD reports internal location codes even when native API has meaningful campus names
tags: ['workday', 'json-ld', 'locations', 'campus']
---
# Workday JSON-LD reports internal location codes even when native API has meaningful campus names

## Problem
Workday JSON-LD reports internal location codes even when native API has meaningful campus names

## Solution
For Cincinnati Children’s, the native workday scraper returns Burnet Campus, Liberty Campus and Offices at Vernon Place, whereas JSON-LD returns Location S. Compare semantics as well as coverage; verify campus names against official locations pages and retain native scraper when it preserves genuine facilities. Both alternatives returned employment type and date; JSON-LD added no optional fields in tested samples.

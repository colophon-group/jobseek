---
step: select_scraper
symptom: Air Apps native Ashby monitor returns complete required fields but no structured base_salary, while official posting detail pages show compensation.
tags: ['ashby', 'json-ld', 'salary', 'enrichment']
---
# Air Apps native Ashby monitor returns complete required fields but no structured base_salary, while official posting detail pages show compensation.

## Problem
Air Apps native Ashby monitor returns complete required fields but no structured base_salary, while official posting detail pages show compensation.

## Solution
Verify static json-ld on multiple official postings. For airapps, all three sampled detail pages yielded base_salary; retain the native ashby monitor and select json-ld scraper_config with enrich:[base_salary] to add salary while preserving native workplace types. JSON-LD alone omitted onsite workplace type in two samples. Compare native and enriched configurations before choosing.

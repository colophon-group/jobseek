---
step: select_scraper
symptom: ICF Phenom JSON-LD returns HTML-entity-encoded descriptions; naive embedded location mapping loses secondary cities
tags: ['phenom', 'embedded', 'html-unescape', 'multi-location', 'icf']
---
# ICF Phenom JSON-LD returns HTML-entity-encoded descriptions; naive embedded location mapping loses secondary cities

## Problem
ICF Phenom JSON-LD returns HTML-entity-encoded descriptions; naive embedded location mapping loses secondary cities

## Solution
For this tenant use embedded variable phApp.ddo path jobDetail.data.job. Decode description with {path:description,html_unescape:true}; preserve all cities with multi_location[].location || [location]; map Full time and Part time to full_time and part_time. Verify keys and complete descriptions through temporary diagnostic ws field mappings, then restore real title/location fields and rerun samples before feedback. Tested construction role retained Los Angeles, Irvine and Riverside. Do not infer structured workplace or salary from office labels or pay prose.

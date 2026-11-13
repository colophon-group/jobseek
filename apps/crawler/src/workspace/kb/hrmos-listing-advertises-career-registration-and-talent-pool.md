---
step: configure
symptom: HRMOS listing advertises career registration and talent pools alongside vacancies; JSON-LD coverage may fail for the registration page.
tags: ['hrmos', 'json-ld', 'talent-pool', 'url_filter']
---
# HRMOS listing advertises career registration and talent pools alongside vacancies; JSON-LD coverage may fail for the registration page.

## Problem
HRMOS listing advertises career registration and talent pools alongside vacancies; JSON-LD coverage may fail for the registration page.

## Solution
Verify each outlier against its official content before changing scraper. For Unilever Japan, /jobs/0000000_01 is career registration and /jobs/0000000_09 is WAAP talent registration, not vacancies. Exact URL exclusions in hrmos monitor reduced 10 links to 8 genuine jobs and restored 8/8 JSON-LD required-field coverage. Do not exclude other providers or future URLs without equivalent evidence. HRMOS validThrough was a rolling five-year placeholder, so do not treat it as a verified application deadline.

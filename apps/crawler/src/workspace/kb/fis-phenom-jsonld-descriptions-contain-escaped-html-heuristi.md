---
step: select_scraper
symptom: FIS Phenom JSON-LD descriptions contain escaped HTML; heuristic embedded employmentType maps hiring experience rather than work schedule.
tags: ['phenom', 'fis', 'embedded', 'html-unescape', 'employment-type', 'workday', 'locations']
---
# FIS Phenom JSON-LD descriptions contain escaped HTML; heuristic embedded employmentType maps hiring experience rather than work schedule.

## Problem
FIS Phenom JSON-LD descriptions contain escaped HTML; heuristic embedded employmentType maps hiring experience rather than work schedule.

## Solution
For careers.fisglobal.com use embedded phApp.ddo at jobDetail.data.job. Decode description with html_unescape:true, read locations from multi_location[].cityStateCountry (fallback cityStateCountry), and normalize jobType values Full time/Part time. employmentType holds Experienced (relevant combo of work and education), not employment schedule; remoteType may be a quote placeholder and must remain absent. Workday source had 510 vs Phenom 503 postings but its sample locations were internal office codes, so the clean Phenom configuration won the required-location quality comparison. Verify future live values before reusing.

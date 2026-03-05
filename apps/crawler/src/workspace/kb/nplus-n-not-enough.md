---
step: verify_and_feedback
symptom: Stats show N/N but extracted content is wrong
tags: [verification, quality, content-check, false-positive]
---
# Stats show N/N but extracted content is wrong

## Problem
Extraction stats show full coverage (N/N for all fields) but the actual
content is garbled, truncated, generic placeholders, or wrong data.

Examples:
- Locations showing "+2 more" instead of full location list
- Descriptions containing only boilerplate or empty HTML tags
- Titles that are actually department names or category headers

## Solution
**Always read the actual extracted data**, not just the stats:

```bash
# For API monitors
cat .workspace/<slug>/artifacts/<alias>/monitor/run-*/jobs.json | python3 -m json.tool | head -80

# For scraper-based monitors
# Read the content samples in ws run scraper output
```

If content is incomplete:
1. Find where the COMPLETE data lives (different API field, different HTML element)
2. Don't apply regex cleanup to broken data — fix the source
3. Try a different scraper type if the current one can't access complete data

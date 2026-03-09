---
step: validate
symptom: Careers page shows more jobs than API returns
tags: ['job-count', 'mismatch', 'validate', 'api']
---
# Job count mismatch between page and API

## Problem
The monitor returns a different number of jobs than what is visible on the careers page.

## Solution

**API/monitor returns MORE than the page shows:** This is normal. APIs often include
unlisted, regional, or hidden postings not rendered in the default careers page view.
As long as the extracted content is clean (real titles, real descriptions), the higher
count is correct. Do not reject a monitor for returning more jobs than the page shows.

**API/monitor returns FEWER than the page shows:** The company careers page may count
internal-only, unlisted, or duplicate postings that the public API does not expose.
Use the API count as the source of truth, not the page estimate. If the gap is large,
check pagination config or try a different monitor type.

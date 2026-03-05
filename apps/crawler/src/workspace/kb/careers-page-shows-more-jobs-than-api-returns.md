---
step: validate
symptom: Careers page shows more jobs than API returns
tags: ['job-count', 'mismatch', 'validate', 'api']
---
# Careers page shows more jobs than API returns

## Problem
Careers page shows more jobs than API returns

## Solution
The company careers page may count internal-only, unlisted, or duplicate postings that the public API does not expose. Use the API count as the source of truth, not the page estimate.

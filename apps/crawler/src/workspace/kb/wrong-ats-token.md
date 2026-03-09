---
step: select_monitor
symptom: Probe detects ATS but API returns wrong or empty data
tags: [greenhouse, lever, ashby, token, api]
---
# Probe detects ATS but API returns wrong or empty data

## Problem
Auto-detection identifies the correct ATS type (e.g., Greenhouse, Lever)
but the API token extracted from the URL is wrong, returning 0 jobs or
a different company's jobs.

## Solution
1. Inspect the board URL page source for the correct token/board ID
2. Check for redirects — the actual ATS board may be on a different subdomain
3. For Greenhouse: look for `data-board-token`, `Grnhse.Settings.boardToken`, or
   `urlToken` in page source. On regional hosts like
   `job-boards.eu.greenhouse.io/<token>`, token is the first path segment.
4. For Lever: the token is the path segment after `jobs.lever.co/`
5. Manually set the correct token:
   ```bash
   ws select monitor greenhouse --config '{"token": "correct-token"}'
   ```

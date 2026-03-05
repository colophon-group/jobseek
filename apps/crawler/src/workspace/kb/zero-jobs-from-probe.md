---
step: select_monitor
symptom: All monitor probes return 0 jobs
tags: [probe, zero-jobs, api-discovery, deep-probe]
---
# All monitor probes return 0 jobs

## Problem
`ws probe monitor` returns 0 jobs for all monitor types, even though
the careers page visibly shows listings.

## Solution
1. Run `ws probe deep -n <count>` for Playwright-based API detection.
   Check its output for script URL discoveries and CMS detection.

2. If deep probe also finds nothing, inspect the page source for API URLs:
   ```bash
   curl -s "<board-url>" -o /tmp/page.html
   grep -oE 'fetch\(["'"'"'][^"'"'"']+|/api/|/wp-json/' /tmp/page.html
   ```

3. If a candidate URL is found: `ws probe api <url>`

4. As a last resort, try dom monitor with `render: true`.

---
step: select_monitor
symptom: "iCIMS board returns inconsistent results with render:true — use static fetch instead"
tags: [icims, dom, render, static, inconsistent]
---
# iCIMS board returns inconsistent results with render:true

## Problem
iCIMS career pages may return different results depending on whether Playwright
rendering is used. With `render: true`, the page may show a different set of
jobs on each load because JavaScript-driven content varies between runs
(deferred loading, client-side filtering, or A/B testing). With `render: false`
(static HTML fetch), the server returns consistent server-rendered HTML
containing the full job listing.

## Solution
Set `render: false` (or omit it entirely) in the DOM monitor config for iCIMS
boards. iCIMS serves job listings as server-side HTML, so browser rendering is
unnecessary and introduces inconsistency.

```bash
ws select monitor dom --config '{"render": false, "url_filter": "/job/"}'
ws run monitor
```

Verify by comparing results from multiple static fetches — the job count and
listing order should remain stable across runs. If static fetch also returns
inconsistent results, the issue is likely server-side caching or load-balancer
routing rather than a rendering problem.

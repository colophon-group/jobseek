---
step: select_monitor
symptom: Sitemap returns non-job URLs
tags: [sitemap, url_filter, blog, mixed-content]
---
# Sitemap returns non-job URLs

## Problem
Sitemap includes blog posts, news articles, and other non-job pages
alongside job listings, inflating the job count.

## Solution
Add `url_filter` to the monitor config to match only job URLs:

```bash
ws select monitor sitemap --config '{"url_filter": "/jobs/"}'
```

Common filter patterns:
- `/jobs/` or `/careers/` for path-based filtering
- `/positions/` for some ATS systems
- `/vacancies/` for EU-style sites

## URL rewriting

If the sitemap lists URLs that are not the public job pages (e.g. authenticated
portal paths, redirect URLs), use `url_transform` to rewrite them:

```bash
ws select monitor sitemap --config '{"url_transform": {"find": "/profile/job_details/", "replace": "/jobs/"}}'
```

`url_transform` uses regex find/replace and is applied after `url_filter`.

If no consistent URL pattern exists, switch to dom or api_sniffer monitor.

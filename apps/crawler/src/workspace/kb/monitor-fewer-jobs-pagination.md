---
step: select_monitor
symptom: Monitor returns fewer jobs than the website shows
tags: [pagination, load-more, multi-page, count-mismatch]
---
# Monitor returns fewer jobs than the website shows

## Problem
Monitor finds some jobs but fewer than the website displays. The careers page
uses pagination (`?page=1`, `?page=2`) or a "Load More" button.

## Solution
For paginated pages, add `pagination` config to the dom monitor:
```json
{"render": false, "url_filter": "/jobs/", "pagination": {"param_name": "page", "max_pages": 10000}}
```

Set `max_pages` to a value that greatly overshoots the expected real page count.
Do not pick a low cap just to reduce crawl time — that trades away completeness
and violates monitor resilience goals. The dom monitor already stops early when
no new URLs are found, so overshooting is usually cheap on small boards.

For "Load More" / "Show More" buttons, use the `repeat` action with `render: true`:
```json
{"render": true, "actions": [{"action": "repeat", "selector": "button.load-more", "max": 30, "wait_ms": 2000}]}
```

Run `ws help monitor dom` for full pagination and action config reference.

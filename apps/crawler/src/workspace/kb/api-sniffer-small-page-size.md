---
step: select_monitor
symptom: api_sniffer finds fewer jobs than expected
tags: [api_sniffer, pagination, page-size, api]
---
# api_sniffer finds fewer jobs than expected

## Problem
api_sniffer captures the API but returns only a subset of jobs (e.g., 10 or 20)
because the API uses a small default page size.

## Solution
After selecting api_sniffer, inspect the auto-filled `api_url` for page size
parameters and increase them:

- `result_limit=10` → `result_limit=100`
- `per_page=20` → `per_page=100`
- `pageSize=10` → `pageSize=100`
- `limit=25` → `limit=100`

Update `pagination.increment` to match the new page size.

```bash
ws select monitor api_sniffer --config '{"api_url": "...&limit=100", "pagination": {"increment": 100, ...}}'
ws run monitor
```

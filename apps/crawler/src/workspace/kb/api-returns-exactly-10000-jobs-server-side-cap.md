---
step: select_monitor
symptom: "API returns exactly 10000 jobs — server-side cap truncates results silently"
tags: [api_sniffer, cap, round-number, faceting, splitting]
---
# API returns exactly 10000 jobs — server-side cap

## Problem
Some APIs (e.g., ByteDance) enforce server-side result caps — commonly 10000,
5000, or 1000. The API returns a suspiciously round number as the total count
without indicating truncation. `ws run monitor` warns about round numbers but
the agent may not act on the warning, leaving jobs beyond the cap uncollected.

## Solution
Split the board into multiple sub-boards by facet (e.g., `job_category_id`,
`region`, `department`). Query the API for available facet values, then create
one board per facet value, ensuring each facet partition stays under the cap
with at least 2x headroom.

1. Inspect the API response for available filters/facets:
   ```bash
   curl -s "<api_url>" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('filters', {}), indent=2))"
   ```

2. Create one board per facet value:
   ```bash
   ws add board <slug>-engineering --url "<board-url>?category=engineering"
   ws add board <slug>-sales --url "<board-url>?category=sales"
   ```

3. Verify the sum of faceted counts exceeds the original capped total — if
   the sum is significantly higher, the cap was indeed truncating results.

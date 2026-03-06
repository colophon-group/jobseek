# Step: Select and Test Monitor

**Board {board_progress}**: `{board_url}`

## Known ATS? Select directly

If the board URL matches a known ATS (see the table in the previous step), select it
directly — no probing needed:

```bash
ws select monitor <type>
ws run monitor
```

## Otherwise, select based on probe results

`ws probe monitor` (from the previous step) tried all types. Select the best detected
monitor and test it:

```bash
ws select monitor <type>
ws run monitor
```

Use `--as <name>` to try alternative configurations without losing the previous one:

```bash
ws select monitor sitemap --as sitemap-filtered --config '{{"url_filter": "/jobs/"}}'
ws run monitor
```

## Verify the results

After `ws run monitor`, check **both** the count and the content:

1. **Count** — compare the job count against the website's displayed total.
   If the count is lower, the monitor may need pagination config or a different type —
   run `ws task troubleshoot 'fewer jobs'`.
2. **Content** — `ws run monitor` prints "Extracted content:" with sample field values
   for rich monitors. Read these samples and verify titles are real job titles,
   descriptions contain meaningful content, and locations are actual place names.
   Populated fields are NOT necessarily correct — verify the text makes sense.

## If the probe returned 0 jobs

The previous step confirmed listings exist, so 0 results means the probe couldn't
detect them — not that they don't exist. Run `ws task troubleshoot 'zero jobs'` for
the escalation path (deep probe, API discovery, dom fallback).

## How to choose between options

When multiple monitors are detected, prefer in this order:

1. **Coverage** — all jobs must be discovered. Full coverage always wins.
2. **Required fields** — title and description must extract for every job.
3. **Resilience** — API > sitemap > dom. Avoid relying on elements that vary between
   job postings or change on redesign (CSS classes, DOM structure). Simpler configs
   over complex ones.
4. **Important fields** — locations and job_location_type when available.
5. **Speed/cost** — among equivalent configs, prefer cheaper and `render: false`.

Run `ws help monitor <type>` for config details on any specific monitor type.

{rejected_configs}

## When done

```bash
ws task next --notes "<monitor type chosen, job count, any issues>"
```

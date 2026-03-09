# Step: Select and Test Monitor

**Board {board_progress}**: `{board_url}`

Before selecting a monitor, confirm this URL is an actual listings board.
If it is only a landing page that links to jobs elsewhere, go back to Step 2,
add the real listings URL as a board, and continue on that board.

## Known ATS? Select directly

If the board URL matches a known ATS (see the table in the previous step), select it
directly — no probing needed:

```bash
ws select monitor <type>
ws run monitor
```

## If `ws run monitor` fails or returns errors

**Do not stop on errors.** Follow this recovery flow:

1. **Read the error message** — it often suggests the fix (wrong domain, missing feed, etc.)
2. **Try `ws probe monitor -n <N>`** to discover what actually works for this board
3. **Try alternative monitor types** — ATS detection is a hint, not a guarantee.
   Some ATS providers have multiple API versions or regional variants.
4. **Check `ws help monitor <type>`** for config options and troubleshooting
5. **Run `ws task troubleshoot '<error summary>'`** for escalation guidance

If the error indicates token/ID extraction failed from a non-ATS URL, the board URL is
likely wrong for that monitor. Add/select the real ATS board URL first, then retry.

If the URL is a regional Greenhouse board like
`https://job-boards.eu.greenhouse.io/<token>`, the token is the first path segment.
If auto-detection still misses it, set it explicitly:

```bash
ws select monitor greenhouse --config '{"token":"<token>"}'
ws run monitor
```

A known ATS URL does not guarantee the expected API is available. APIs change,
feeds get deprecated, and regional variants may use different endpoints.
Always verify with `ws run monitor` and fall back to probing if it fails.

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

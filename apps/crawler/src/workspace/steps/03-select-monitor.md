# Step: Select and Test Monitor

**Board {board_progress}**: `{board_url}`

Mindset: monitor detection is a hypothesis. Decide using evidence quality and
coverage, not by taking the first suggestion.

Before selecting a monitor, confirm this URL is an actual listings board.
If it is only a landing page that links to jobs elsewhere, go back to Step 2,
add the real listings URL as a board, and continue on that board.

## Known ATS? Select directly

If the board URL clearly matches a known ATS/feed (for example Greenhouse, Lever,
Ashby, Workday, JOIN, SuccessFactors/Teamtailor via `rss`), select that monitor directly —
no probing needed:

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

## Configuration-first loop (strong default)

If the chosen monitor is plausible but results are wrong (0 jobs, low count, or missing
required fields), iterate config for the **same monitor type** before changing types.

1. Read the type docs: `ws help monitor <type>`
2. Apply a concrete config change with `--as <name>` (keep previous attempts)
3. Re-run: `ws run monitor`
4. Compare count + extracted content again

Only switch to another monitor type when:
- the current type is clearly a mismatch for the board, or
- at least one targeted config iteration for that type still fails.

Before changing monitor type, use this evidence gate:
- Do not switch after the first failed/incomplete run unless there is a hard mismatch
  (wrong platform/domain, unsupported endpoint, or explicit non-detection).
- For a plausible monitor, try at least one concrete config variant first:
  `ws select monitor <type> --as <name> --config '{...}'`
- Record rejected attempts and reasons:
  `ws reject-config <name> --reason "<why it failed>"`

Where to look for config details and debugging context:
- `ws help monitor <type>`
- `ws help monitors`
- `ws help actions` (for `render: true` flows)
- `ws help artifacts` (what files to inspect after each run)
- `ws help troubleshooting` / `ws task troubleshoot '<symptom>'`

## Verify the results

After `ws run monitor`, check **both** the count and the content:

1. **Count** — compare the job count against the website's displayed total.
   If the count is **lower**, the monitor may need pagination config or a different type —
   run `ws task troubleshoot 'fewer jobs'`.
   For paginated monitors (`dom`, `api_sniffer`), set `max_pages` to a value
   that significantly overshoots the expected real page count, then rely on
   "stop when no new jobs" behavior. Avoid conservative caps that undercount jobs.
   If the count is **higher** than the visible page total, that is normal and expected —
   APIs often include unlisted, regional, or hidden postings not shown in the default
   careers page view. As long as the extracted content is clean (real titles, real
   descriptions), the higher count is correct. **Do not reject a monitor for returning
   more jobs than the page shows.**
2. **Content** — `ws run monitor` prints "Extracted content:" with sample field values
   for rich monitors. Read these samples and verify titles are real job titles,
   descriptions contain meaningful content, and locations are actual place names.
   Populated fields are NOT necessarily correct — verify the text makes sense.
3. **Filter safety** — if using `url_filter` or manual `job_link_pattern`, validate
   regex coverage before accepting the config:
   - Run a baseline without restrictive filtering (or with a broad include), then
     compare with the filtered run.
   - The filtered run must still match expected listing count.
   - Include common URL variants in regexes: optional numeric suffixes,
     trailing slash, and query params.
   - If one visible posting is missing, treat the regex as too strict and widen it
     before continuing.

## If the probe returned 0 jobs

The previous step confirmed listings exist, so 0 results means the probe couldn't
detect them — not that they don't exist. Run `ws task troubleshoot 'zero jobs'` for
the escalation path (deep probe, API discovery, dom fallback).

## How to choose between options

When multiple monitors are detected, this order is a heuristic for interpreting evidence:

1. **Coverage** — all jobs must be discovered. A monitor returning more jobs than the
   page shows is a *superset*, not a problem — prefer it over a page-matching monitor.
2. **Required fields** — title and description must extract for every job.
3. **Resilience** — API monitors > nextdata/api_sniffer > sitemap/umantis > dom.
   Avoid relying on elements that vary between job postings or change on redesign
   (CSS classes, DOM structure). Simpler configs over complex ones.
   An api_sniffer or API monitor that works is almost always better than dom —
   it is faster, cheaper, and immune to frontend redesigns.
4. **Important fields** — locations and job_location_type when available.
5. **Speed/cost** — among equivalent configs, prefer cheaper and `render: false`.

Run `ws help monitor <type>` for config details on any specific monitor type.
If signals conflict, explain why one signal is stronger (for example, direct site
references and coverage parity vs blind slug detections).

{rejected_configs}

## When done

```bash
ws task next --notes "<monitor type chosen, job count, any issues>"
```

# Step: Select and Test Monitor

**Board {board_progress}**: `{board_url}`

Select the best monitor type based on probe results and test it.

## Known ATS — skip straight to select

If the board URL matches a known ATS (see the table in the previous step), skip probe
results and select directly. These are rich API monitors — they return full job data
and the scraper step will be auto-skipped.

```bash
ws select monitor <type>
ws run monitor
```

## Configuration priorities (in order)

1. **Coverage** — all jobs must be discovered. Full coverage always wins.
2. **Required fields** — title and description must extract for every job.
3. **Resilience** — API > sitemap > dom; simpler configs > complex configs.
4. **Important fields** — locations and job_location_type when available.
5. **Speed/cost** — among equivalent configs, prefer cheaper and `render: false`.

## Select and run

```bash
ws select monitor <type>
ws run monitor
```

Use `--as <name>` to try multiple configurations under different names:

```bash
ws select monitor sitemap --as sitemap-filtered --config '{{"url_filter": "/jobs/"}}'
ws run monitor
```

After the test crawl, **compare the job count against the website's displayed total**.
If counts don't match, iterate.

## API monitors (most resilient)

Rich API monitors return full data and skip the scraper step entirely:
`greenhouse`, `lever`, `ashby`, `personio`, `pinpoint`, `recruitee`,
`rippling`, `rss`, `smartrecruiters`, `workable`, `workday`, `hireology`.
Always use them when detected by probe or when the URL matches.

## api_sniffer

When no known ATS API exists but the site loads data via internal APIs, `api_sniffer`
captures those APIs. After selecting, inspect the auto-filled `api_url` for page size
parameters (e.g. `result_limit=10`) and increase them if the API allows.

## Zero jobs

Step 1 confirmed listings exist, so 0 results = misconfiguration. Try:

1. Different monitor types from probe results
2. API discovery fallback:
   ```bash
   curl -s "{board_url}" -o /tmp/page.html
   grep -oE 'fetch\(["'"'"'][^"'"'"']+|/api/|/wp-json/' /tmp/page.html
   ```
3. If a candidate URL is found: `ws probe api <url>`
4. `ws probe deep -n <count>` for Playwright-based detection

## Multi-page sites

For pagination, use `pagination` config on the DOM monitor:
```json
{{"render": false, "url_filter": "/jobs/", "pagination": {{"param_name": "page", "max_pages": 15}}}}
```

For "Load More" buttons:
```json
{{"render": true, "actions": [{{"action": "repeat", "selector": "button.load-more", "max": 30, "wait_ms": 2000}}]}}
```

{rejected_configs}

## When done

The gate auto-checks: monitor must be selected and tested with >0 jobs.

```bash
ws task next --notes "<monitor type chosen, job count, any issues>"
```

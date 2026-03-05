# Step: Verify Quality and Record Feedback

**Board {board_progress}**: `{board_url}`

**This step applies to ALL monitor types, including rich API monitors.** Do not skip it.

## Verify extracted content

N/N stats are not enough — **read the actual data**.

### For API monitors

```bash
cat {artifact_path}/monitor/run-*/jobs.json | python3 -m json.tool | head -80
```

### For scraper-based monitors

Read the content samples in `ws run scraper` output.

### For all types, verify:

- **Titles** are real job titles (not garbled, truncated, or placeholder text)
- **Descriptions** contain meaningful content (not empty HTML or boilerplate)
- **Locations** are actual place names (not codes, IDs, or "+2 more" truncations)
- A populated field is NOT necessarily correct — verify actual text makes sense

### Check for additional fields

Look for mappable fields in the raw data (same source, no extra requests):
`employment_type`, `date_posted`, `job_location_type`, team/department (`metadata.*`),
`base_salary`, `qualifications`, `responsibilities`.

Run `ws help fields` for accepted formats and values.

If you find additional fields, update the config and re-run:

```bash
ws select scraper <type> --config '<updated config>'
ws run scraper
```

## Record feedback

**Mandatory before submit.** Every config must have feedback with `--verdict-notes`.

```bash
ws feedback --title clean --description clean \
  --locations clean --employment-type clean \
  --job-location-type clean --date-posted clean \
  --verdict good --verdict-notes "<brief explanation>"
```

**Quality values per field:** `clean`, `noisy`, `unusable`, `absent`

**Verdict levels:**
- `good` — all required fields clean, important fields mostly clean
- `acceptable` — required fields clean, some important fields noisy
- `poor` — required fields noisy or important fields absent (submit with `--force`)
- `unusable` — required fields unusable (try another config)

Omit field flags only for fields with 0 coverage.
The `--verdict-notes` should explain what happened (one sentence).

## If verdict is poor or unusable

```bash
ws reject-config <name> --reason "Locations missing, titles truncated"
```

Then go back and try a different config — use `ws task fail` if all options are exhausted.

## When done

The gate auto-checks: feedback must be recorded with `good` or `acceptable` verdict.

```bash
ws task next --notes "<verdict, quality summary, any concerns>"
```

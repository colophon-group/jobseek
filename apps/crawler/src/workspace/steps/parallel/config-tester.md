# Config Tester: {{ monitor_type }}{% if scraper_type and scraper_type != 'skip' %} + {{ scraper_type }}{% endif %}

Board: `{{ board_alias }}` (`{{ board_url }}`)
Workspace: `{{ slug }}`
Config name: `{{ config_name }}`
Expected jobs: ~{{ expected_jobs }}


> **STOP — Do NOT read source code files.** Never open files under
> `src/core/`, `src/shared/`, `src/workspace/`, or any `.py` file.
> Use `ws help monitor <type>` and `ws help scraper <type>` for config
> reference. If stuck, use `ws task troubleshoot "<query>"`.

## Assignment

Test whether **{{ monitor_type }}**{% if scraper_type and scraper_type != 'skip' %} (monitor) + **{{ scraper_type }}** (scraper){% endif %} can produce a working config for this board. The probe estimated ~{{ expected_jobs }} jobs at ~{{ cost_estimate }}/cycle.
{% if is_rich %}
This monitor returns **rich data** (full job objects) — scraper may be auto-skipped.
{% endif %}
{% if prior_context %}
### Prior context

{{ prior_context }}
{% endif %}

## Step 1: Select monitor

```bash
ws select monitor {{ slug }} {{ monitor_type }} --as {{ config_name }} --board {{ board_alias }} --config '{{ monitor_config }}'
```

If this fails, check `ws help monitor {{ monitor_type }}` for config options.

## Step 2: Run monitor

```bash
ws run monitor {{ slug }} --board {{ board_alias }} --config {{ config_name }}
```

**Important:** The first argument is the workspace slug (`{{ slug }}`),
NOT the config name. The config name goes in `--config`. Passing a config
name like `dom-test` as the slug causes "Workspace not found" errors.

### If you get HTTP 403/406 errors

Many career sites block non-browser requests. Common fixes:
- Add `"render": true` to the monitor config (uses a real browser)
- Try a different monitor type — `dom` with `render: true` bypasses most blocks
- Run `ws task troubleshoot 'http 403'` for site-specific workarounds

### Verify job count

Compare the crawled job count against the expected ~{{ expected_jobs }} jobs.
- **Within 15%:** Good — proceed to scraper.
- **Significantly lower:** Monitor is misconfigured. Check config options
  (`ws help monitor {{ monitor_type }}`), try a config variation before
  reporting failure.
- **Significantly higher:** May be fine (superset detection), but verify
  URLs are real job pages, not category/tag pages.
- **0 jobs:** Try `ws task troubleshoot 'zero jobs'` for tips. Do NOT
  proceed — report failure if you cannot fix it.
- **Suspiciously low for a large company?** If the company has hundreds
  or thousands of employees but the monitor found fewer than ~10 jobs,
  something is likely wrong — the monitor may be hitting a filtered view,
  a regional subset, or a paginated first page only. Investigate before
  accepting. Report this concern in your feedback verdict-notes.
{% if not is_rich %}

## Step 3: Select and run scraper

**Scraper priority — always try json-ld first:**
1. `json-ld` — extracts structured `JobPosting` schema (most career pages have
   it). Use `render: true` if JS-rendered. Tune `timeout` if coverage < 100%.
2. `embedded` / `nextdata` — for embedded JSON with field mapping.
3. `dom` — last resort, step-based CSS extraction.

```bash
ws select scraper {{ slug }} {{ scraper_type }} --config '{{ scraper_config }}'
ws run scraper {{ slug }} --board {{ board_alias }} --config {{ config_name }}
```

If scraper fails or extracts poorly, check `ws help scraper {{ scraper_type }}`
for config options. Try one config variation before reporting failure.
{% endif %}

## Step {{ '3' if is_rich else '4' }}: Verify extraction quality

Read the "Extracted content:" output from the run command. For 2-3 sample
jobs, verify:

- **Titles** are real job titles (not garbled, truncated, or navigation text)
- **Descriptions** contain structured content — responsibilities, requirements,
  qualifications, or project context. A one-liner, bare title, or "apply now"
  is NOT acceptable.
- **Locations** are real place names (not codes, IDs, or "null")
- Fields that show as present are semantically correct, not just non-empty

**Required fields (non-negotiable):** title, location, description.
If any required field is missing or unusable across all samples, this
config fails — report failure.

### Maximize field coverage

**Map every available field from the data source.** Don't stop at the
required fields — inspect the raw API response or HTML for all extractable
data. For structured sources (API, JSON-LD, embedded JSON), check every
key in the response and map it to a job posting field if applicable.

Fields to look for beyond title/location/description:
- `employment_type` (full_time, part_time, contract, internship, temporary)
- `job_location_type` (onsite, hybrid, remote)
- `date_posted` (posting date)
- `salary` (min, max, currency, period)
- `qualifications` / `responsibilities` (HTML text)

For **rich monitors** (api_sniffer with `fields`): map everything
available in the list API response. Use `enrich: ["description"]` to
fill in fields only available from the detail API, rather than scraping
all fields redundantly.

### Improve noisy or absent fields

- **`employment_type` / `job_location_type` noisy?** Use the `map` spec to
  normalize non-standard values (see `ws help fields`):
  `"employment_type": {"path": "jobType", "map": {"Regular": "full_time"}}`
- **Fields absent but available in JSON-LD?** Switch scraper to `json-ld` —
  it often extracts `datePosted`, `employmentType`, `baseSalary`,
  `jobLocationType` that DOM scrapers miss.
- **Monitor has partial data (titles but no locations)?** Try the `enrich`
  option to scrape only missing fields from detail pages (see `ws help
  scraper api_sniffer`).
- **Locations absent but all jobs are genuinely in one city/region?**
  As a **last resort** (after trying json-ld, enrich, and DOM), use
  `"defaults"` to set a constant: `"defaults": {"locations": ["Zurich, CH"]}`.
  Only valid when you've verified every job is in that location. Do NOT
  use defaults to mask extraction failures — always try proper extraction first.
  Report defaults in verdict-notes so reviewers know.

## Step {{ '4' if is_rich else '5' }}: Record feedback

Include ALL fields that appear in the extraction output. Check the run
output for field counts — every field with count > 0 needs a quality rating.

```bash
ws feedback {{ slug }} {{ config_name }} --board {{ board_alias }} \
  --title <quality> --description <quality> --locations <quality> \
  --employment-type <quality> --job-location-type <quality> \
  --date-posted <quality> --base-salary <quality> \
  --verdict <level> --verdict-notes "<brief explanation>"
```

Quality values: `clean`, `noisy`, `unusable`, `absent`
Verdict: `good`, `acceptable`, `poor`, `unusable`

Use `absent` for fields with 0 coverage. Omit optional fields only
if they had 0 jobs in the extraction output.

## Report

After testing, report your result to the main agent. Include:

- **Status:** success or failure
- **Job count:** crawled vs expected ({{ expected_jobs }})
- **Required fields:** title (quality), description (quality), locations (quality)
- **Optional fields:** which ones are present and their quality
- **Cost:** measured time per cycle
- **Verdict:** your feedback verdict and brief explanation
- **Issues:** anything unexpected (URL redirects, missing pages, partial data)

## Important

- **Failure is acceptable.** If this combination does not work, say so
  clearly. Do not force a passing verdict on broken extraction.
- **Try one config iteration** before giving up — check `ws help` for the
  monitor/scraper type to see what options are available.
- **Use `--config {{ config_name }}`** on run commands to avoid conflicts
  with other subagents testing different configs on the same board.
- **NEVER read source code** — not `src/core/`, `src/shared/`, nor any
  `.py` files. This is a hard rule. Reading source wastes tokens and does
  not help configure boards. Use `ws help monitor <type>` and `ws help
  scraper <type>` for config reference. If stuck: `ws task troubleshoot "<query>"`.

# Config Tester: {{ monitor_type }}{% if scraper_type and scraper_type != 'skip' %} + {{ scraper_type }}{% endif %}

Board: `{{ board_alias }}` (`{{ board_url }}`)
Workspace: `{{ slug }}`
Config name: `{{ config_name }}`
Expected jobs: ~{{ expected_jobs }}


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
ws select monitor {{ monitor_type }} --as {{ config_name }} --board {{ board_alias }} --config '{{ monitor_config }}'
```

If this fails, check `ws help monitor {{ monitor_type }}` for config options.

## Step 2: Run monitor

```bash
ws run monitor --board {{ board_alias }} --config {{ config_name }}
```

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
{% if not is_rich %}

## Step 3: Select and run scraper

```bash
ws select scraper {{ scraper_type }} --config '{{ scraper_config }}'
ws run scraper --board {{ board_alias }} --config {{ config_name }}
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

## Step {{ '4' if is_rich else '5' }}: Record feedback

```bash
ws feedback {{ config_name }} --board {{ board_alias }} \
  --title <quality> --description <quality> --locations <quality> \
  --employment-type <quality> --job-location-type <quality> \
  --verdict <level> --verdict-notes "<brief explanation>"
```

Quality values: `clean`, `noisy`, `unusable`, `absent`
Verdict: `good`, `acceptable`, `poor`, `unusable`

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

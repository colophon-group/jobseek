---
step: select_monitor
symptom: "api_sniffer auto-probe selects wrong json_path — picks filter/facet array instead of job listings"
tags: [api_sniffer, json_path, auto-detection, wrong-array]
---
# api_sniffer auto-probe selects wrong json_path

## Problem
The auto-probe's json_path detection picks the first sizable array it finds in
the API response. For APIs that return multiple arrays (e.g., filters, facets,
categories alongside actual job listings), it may select the wrong one.
Symptoms: extracted "jobs" are actually filter values (countries, categories,
departments) with a wrong field structure — titles look like category names,
locations are missing, and URLs are malformed or absent.

## Solution
Inspect the raw API response to find the correct array containing job objects.

1. Fetch the raw response and examine its structure:
   ```bash
   curl -s "<api_url>" | python3 -c "
   import sys, json
   data = json.load(sys.stdin)
   def show(obj, path=''):
       if isinstance(obj, list) and len(obj) > 0:
           print(f'{path} -> list[{len(obj)}] first keys: {list(obj[0].keys()) if isinstance(obj[0], dict) else type(obj[0]).__name__}')
       elif isinstance(obj, dict):
           for k, v in obj.items():
               show(v, f'{path}.{k}')
   show(data)
   "
   ```

2. Look for the array containing objects with job-like fields (`title`,
   `location`, `url`/`id`, `description`).

3. Override `json_path` manually in the config:
   ```bash
   ws select monitor api_sniffer --config '{"json_path": "data.jobs"}'
   ws run monitor
   ```

4. Verify by checking that extracted titles are real job titles, not category
   names or filter labels.

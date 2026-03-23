---
step: select_monitor
symptom: "api_sniffer paginates through items but emits 0 jobs — HTTP 405 on replay"
tags: [api_sniffer, POST, 405, zero-jobs, header-refresh]
---
# api_sniffer paginates but emits 0 jobs — HTTP 405 on replay

## Problem
The api_sniffer successfully detects and paginates through an API during
discovery (reporting item counts at each page) but emits 0 actual jobs in the
final output. Inspecting the http_log artifact reveals the replay request
receives a 405 Method Not Allowed — the API requires POST but the sniffer
replayed with GET.

## Solution
Check the sniffer's captured method in the config. If the original request
was POST, ensure the config preserves `method: POST` and includes the correct
`post_data`.

1. Inspect the http_log for the 405 response:
   ```bash
   # Look for method mismatch in the run artifacts
   ws run monitor  # then check artifacts for http_log
   ```

2. If the sniffer auto-configured as GET, manually override:
   ```bash
   ws select monitor api_sniffer --config '{"method": "POST", "post_data": {"offset": 0, "limit": 100}}'
   ws run monitor
   ```

3. Verify that the `post_data` matches the original request body captured
   during discovery. Missing or malformed `post_data` can also cause a 405
   or 400 response even when the method is correctly set to POST.

---
step: select_monitor
symptom: "api_sniffer sends GET instead of POST after header-refresh on faceted board URLs"
tags: [api_sniffer, POST, GET, header-refresh, faceting]
---
# api_sniffer sends GET instead of POST after header-refresh

## Problem
When a board URL includes query parameters for faceting (e.g.,
`?category=engineering`), the api_sniffer's header-refresh step navigates to
that URL but may trigger a GET request instead of the original POST. The
sniffer captures this GET and replays it, receiving HTML instead of JSON.
This typically produces 0 jobs or a parse error.

## Solution
Ensure the board URL triggers the same API call pattern as the original
detection. If the faceted URL doesn't trigger the POST API call during
navigation, use the base URL with `post_data` containing the facet filter
instead.

1. Check the api_sniffer config for `method` and `post_data` overrides:
   ```bash
   ws select monitor api_sniffer --config '{"method": "POST", "post_data": {"category": "engineering", "limit": 100}}'
   ```

2. If the header-refresh is the issue, try using the base (unfaceted) URL as
   the board URL and pass the filter via `post_data` in the config.

3. Verify the correct method is being used by checking the http_log artifact
   after `ws run monitor` — look for the request method and content-type.

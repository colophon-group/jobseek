---
step: select_monitor
symptom: api_sniffer probe detects POST API endpoint but does not capture post_data, causing 400 Bad Request on ws run monitor
tags: ['api_sniffer', 'post_data', '400', 'configuration']
---
# api_sniffer probe detects POST API endpoint but does not capture post_data, causing 400 Bad Request on ws run monitor

## Problem
api_sniffer probe detects POST API endpoint but does not capture post_data, causing 400 Bad Request on ws run monitor

## Solution
Manually configure post_data in the monitor config with a JSON body containing empty/default search params (e.g. keyword, limit, offset). The API auto-fill captures the URL and headers but may miss the POST body.

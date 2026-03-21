---
step: select_monitor
symptom: api_sniffer probe captures page_number param starting at page 2 (from pagination click), and direct HTTP fetch returns 403 Forbidden
tags: ['api_sniffer', 'pagination', 'browser', '403', 'cookies']
---
# api_sniffer probe captures page_number param starting at page 2 (from pagination click), and direct HTTP fetch returns 403 Forbidden

## Problem
api_sniffer probe captures page_number param starting at page 2 (from pagination click), and direct HTTP fetch returns 403 Forbidden

## Solution
Manually set pagination config with start_value=1 and enable browser:true to establish cookies before API calls. The probe's captured params often reflect the second page (triggered by pagination click), not the first.

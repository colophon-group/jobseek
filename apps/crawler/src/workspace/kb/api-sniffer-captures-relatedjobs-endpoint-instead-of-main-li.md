---
step: select_monitor
symptom: API sniffer captures related-jobs endpoint instead of main listing endpoint
tags: ['api_sniffer', 'pagination', 'wrong-endpoint']
---
# API sniffer captures related-jobs endpoint instead of main listing endpoint

## Problem
API sniffer captures related-jobs endpoint instead of main listing endpoint

## Solution
Check probe logs for all captured URLs. The /api/apply/v2/jobs/{id}/jobs endpoint is scoped to related jobs. Use /api/apply/v2/jobs?domain=<domain> as the base listing endpoint with offset pagination (start/num params).

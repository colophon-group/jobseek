---
step: test_config
symptom: iCIMS specialty portals appear independent in ws compare-boards because cross-host URLs differ, even when requisition IDs are shared.
tags: ['icims', 'overlap', 'subset', 'job-id', 'deduplication']
---
# iCIMS specialty portals appear independent in ws compare-boards because cross-host URLs differ, even when requisition IDs are shared.

## Problem
iCIMS specialty portals appear independent in ws compare-boards because cross-host URLs differ, even when requisition IDs are shared.

## Solution
Verify official ownership and at least one shared requisition first. Then use the native icims dedupe_job_ids_from_hosts option in temporary named specialty configs to compare full peer inventories by requisition ID. On Legacy Health, the main careers-lhs portal returned 505 jobs; complete nursing-lhs and provider-lhs crawls returned 100 and 111 jobs, and deduplication against careers-lhs removed all specialty IDs with no truncation. Remove confirmed subset boards instead of treating different hostnames or capped stored URL samples as proof of distinct jobs. Do not use peer deduplication across unrelated tenants.

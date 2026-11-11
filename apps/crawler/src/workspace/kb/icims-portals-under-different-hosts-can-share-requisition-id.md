---
step: test_config
symptom: iCIMS portals under different hosts can share requisition IDs while ws compare-boards reports no URL overlap; large monitor artifacts may show only the first 100 URLs.
tags: ['icims', 'multiple-boards', 'deduplication', 'overlap']
---
# iCIMS portals under different hosts can share requisition IDs while ws compare-boards reports no URL overlap; large monitor artifacts may show only the first 100 URLs.

## Problem
iCIMS portals under different hosts can share requisition IDs while ws compare-boards reports no URL overlap; large monitor artifacts may show only the first 100 URLs.

## Solution
Verify official links and shared posting identity, then use the native icims dedupe_job_ids_from_hosts config to compare complete portal inventories and remove proven shared requisitions. Gilbane main had 442 jobs; craft had 44, of which 30 matched main and 14 were exclusive. Keep craft with main as peer. Campus had 14 and zero main overlap, so keep without a peer crawl. Check small residual sets by requisition ID as well; distinct hosts alone do not prove independent jobs.

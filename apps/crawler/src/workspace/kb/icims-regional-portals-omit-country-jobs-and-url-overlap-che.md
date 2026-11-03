---
step: configure
symptom: iCIMS regional portals omit country jobs and URL overlap checks miss shared requisitions across different public hosts.
tags: ['icims', 'aggregate', 'overlap', 'deduplication', 'expleo']
---
# iCIMS regional portals omit country jobs and URL overlap checks miss shared requisitions across different public hosts.

## Problem
iCIMS regional portals omit country jobs and URL overlap checks miss shared requisitions across different public hosts.

## Solution
Verify regional portals with explicit job_hosts and require an exact complete country-ID union; replace failing aggregates with official country sources. Compare numeric requisition IDs, not only URLs. Use dedupe_job_ids_from_hosts only after complete native peer runs prove shared requisitions. Expleo retained country sources for Europe, India and Canada, a verified Africa aggregate, and exclusions for 11 UK/NI copies plus corporate requisition52433 shared by Romania, Spain and Morocco. Full native audits closed the 100-URL artifact sampling limit and established992 unique jobs.

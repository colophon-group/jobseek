---
step: configure
symptom: Paycom portals for the same employer appeared independent in compare-boards because portal URLs and titles differed and the smaller portal omitted preview locations.
tags: ['paycom', 'overlap', 'duplicate-portals', 'job-ids']
---
# Paycom portals for the same employer appeared independent in compare-boards because portal URLs and titles differed and the smaller portal omitted preview locations.

## Problem
Paycom portals for the same employer appeared independent in compare-boards because portal URLs and titles differed and the smaller portal omitted preview locations.

## Solution
Compare metadata.job_id sets in generated monitor artifacts before accepting multiple Paycom portals. For Specs, all 104 IDs from portal 9c49c881b4b28b1a4f563ee49ffa158d were included in the 387 IDs from 77164ab11c98cfd58419562775a60505; remove the smaller redundant board and retain the richer superset. Token configs must match their board URL.

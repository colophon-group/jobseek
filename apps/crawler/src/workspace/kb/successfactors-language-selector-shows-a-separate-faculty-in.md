---
step: add_boards
symptom: SuccessFactors language selector shows a separate faculty inventory despite a rich combined feed
tags: ['successfactors', 'rss', 'locale', 'board-overlap']
---
# SuccessFactors language selector shows a separate faculty inventory despite a rich combined feed

## Problem
SuccessFactors language selector shows a separate faculty inventory despite a rich combined feed

## Solution
Do not infer missing coverage from locale counts. For jobs.tec.mx, a temporary English search board with static DOM selector a.jobTitle-link returned 19 URLs; compare-boards proved all 19 were included in the 396-job RSS/googlefeed.xml board. Remove the temporary locale board after proving subset coverage. Official faculty and IT careers landing pages routed back to the same tenant.

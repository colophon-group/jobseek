---
step: configure
symptom: iCIMS regional or language portals can partially mirror requisition IDs while still containing additional live jobs; URL comparisons alone miss duplicates across hosts.
tags: ['icims', 'partial-overlap', 'language-portals', 'deduplication']
---
# iCIMS regional or language portals can partially mirror requisition IDs while still containing additional live jobs; URL comparisons alone miss duplicates across hosts.

## Problem
iCIMS regional or language portals can partially mirror requisition IDs while still containing additional live jobs; URL comparisons alone miss duplicates across hosts.

## Solution
Verify same requisition IDs represent translated copies, then configure the supplemental icims host with dedupe_job_ids_from_hosts listing the verified primary peers. Complete pagination of Spanish Bartlett returned 522 jobs, removed 394 shared IDs against 521 jobs from US/Canada/UK, and retained 128 additional listings. Compare boards after rerunning each final active config: a temporary monitor tested on another board can leave its latest artifacts pointing at the temporary source.

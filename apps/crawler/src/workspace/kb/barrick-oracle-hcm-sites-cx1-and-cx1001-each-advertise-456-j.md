---
step: monitor
symptom: Barrick Oracle HCM sites CX_1 and CX_1001 each advertise 456 jobs but expose 455, causing an incomplete cycle.
tags: ['oracle_hcm', 'barrick-mining', 'tail-overcount', 'mirror']
---
# Barrick Oracle HCM sites CX_1 and CX_1001 each advertise 456 jobs but expose 455, causing an incomplete cycle.

## Problem
Barrick Oracle HCM sites CX_1 and CX_1001 each advertise 456 jobs but expose 455, causing an incomplete cycle.

## Solution
Verify before applying tolerance: repeated default CX_1 runs, an offset_overlap=10 run, and canonical CX_1001 comparison all returned 455 identical title/location identities. Use total_count_tolerance=1 only for this verified stable tenant overcount; do not generalize to unexplained shortfalls. compare-boards confirmed CX_1/CX_1001 are mirrors, so retain only canonical CX_1001. Enrich Oracle detail description, employment_type and job_location_type; workplace type was absent in tested samples.

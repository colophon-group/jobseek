---
step: add_boards
symptom: A branded Jibe/iCIMS mirror reports more jobs than the native iCIMS portal.
tags: ['icims', 'jibe', 'mirror', 'stale', 'board-discovery']
---
# A branded Jibe/iCIMS mirror reports more jobs than the native iCIMS portal.

## Problem
A branded Jibe/iCIMS mirror reports more jobs than the native iCIMS portal.

## Solution
Verify official All Jobs and apply URLs, enumerate requisition IDs across both sources, and check mirror-only IDs on the native detail endpoint before adding another board. For Acts, talent.actslife.org had 514 unique IDs versus 486 native jobs (484 overlap, 30 mirror-only, 2 native-only); three mirror-only samples spanning IDs 15551, 17722 and 19950 returned HTTP 410 explicitly no longer open. Keep the native tenant and exclude the stale mirror; a larger mirror count alone does not establish additional live coverage.

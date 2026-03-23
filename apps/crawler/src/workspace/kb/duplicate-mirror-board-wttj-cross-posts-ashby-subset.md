---
step: add_boards
symptom: "Duplicate/mirror board detected — WTTJ cross-posts subset of Ashby listings"
tags: [mirror-board, duplicate, wttj, ashby, subset, welcometothejungle]
---
# Duplicate/mirror board — WTTJ cross-posts subset of Ashby listings

## Problem
Welcome to the Jungle (WTTJ) boards often cross-post a subset of a company's
Ashby (or other primary ATS) listings. The WTTJ board shows fewer jobs (e.g.,
12 vs 13) with identical titles. Both boards are technically functional but the
WTTJ board adds no unique listings — it is a strict subset of the primary
board.

## Solution
Compare job titles between the two boards to determine if one is a mirror.

1. Fetch both board pages and compare listing titles:
   ```bash
   ws run monitor --config ashby-main
   ws run monitor --config wttj-board
   # Compare job titles in the output
   ```

2. If every WTTJ job also appears on the Ashby board, WTTJ is a mirror — drop
   it and keep only the primary ATS board (Ashby):
   ```bash
   ws del board wttj
   ```

3. If the WTTJ board contains unique listings not present on Ashby, keep both
   boards. This is uncommon but can happen when WTTJ is used for a specific
   region (e.g., France-only roles).

4. The same pattern applies to other aggregator mirrors (LinkedIn-embedded
   boards, Indeed-syndicated listings). Always check whether the secondary
   board adds unique jobs before keeping it.

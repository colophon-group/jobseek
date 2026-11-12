---
step: board_research
symptom: Finiper brand JSON feeds appear to add 148 roles beyond the central SuccessFactors RSS, with different URL and title schemes
tags: ['successfactors', 'finiper', 'stale-feed', 'overlap', 'requisition-id']
---
# Finiper brand JSON feeds appear to add 148 roles beyond the central SuccessFactors RSS, with different URL and title schemes

## Problem
Finiper brand JSON feeds appear to add 148 roles beyond the central SuccessFactors RSS, with different URL and title schemes

## Solution
Compare canonical requisition IDs before treating brand feeds as independent sources. Finiper central RSS marketing IDs resolve on public detail pages to canonical requisition IDs. Of 431 records in the official Unes, Iper and Romantica JSON feeds, 283 overlapped current central IDs and all 148 remaining records explicitly showed unavailable on their canonical RMK pages. A known-live requisition tested with the same route confirmed the availability check. Keep the current group feed and remove stale overlapping brand feeds; URL or title mismatch alone does not prove extra live coverage. Record country-only feed locations and upstream removal lag separately.

---
step: add_boards
symptom: Paycom portals for the same employer can appear disjoint in compare-boards because portal URLs differ and one portal appends requisition numbers to titles.
tags: ['paycom', 'board-overlap', 'subset', 'job-id']
---
# Paycom portals for the same employer can appear disjoint in compare-boards because portal URLs differ and one portal appends requisition numbers to titles.

## Problem
Paycom portals for the same employer can appear disjoint in compare-boards because portal URLs differ and one portal appends requisition numbers to titles.

## Solution
Verify employer identity and compare stable ATS job IDs in the saved monitor jobs.json data before retaining multiple portals. For Priority Management, corporate tenant 50DE6179E7AB2EF5949EC4D6FE9B92DD returned 3 jobs (458459, 406712, 554665), all present in the official B3AC7611080C8BFA4349B9C9A759F80B board of 381 jobs; remove the redundant corporate subset. Shared detail-page accessibility alone does not prove listing membership.

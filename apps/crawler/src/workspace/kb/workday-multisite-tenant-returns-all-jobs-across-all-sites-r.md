---
step: add_boards
symptom: Workday multi-site tenant returns all jobs across all sites regardless of which site URL is used as the board URL
tags: ['workday', 'multi-site', 'deduplication', 'board-discovery']
---
# Workday multi-site tenant returns all jobs across all sites regardless of which site URL is used as the board URL

## Problem
Workday multi-site tenant returns all jobs across all sites regardless of which site URL is used as the board URL

## Solution
When a Workday tenant hosts multiple career sites (e.g., parent + subsidiaries), the monitor aggregates jobs from all sites. Use a single board for the primary site URL — separate boards for subsidiary sites will produce exact duplicates. Verify by comparing job counts: if two different site URLs return the same total, they share a tenant.

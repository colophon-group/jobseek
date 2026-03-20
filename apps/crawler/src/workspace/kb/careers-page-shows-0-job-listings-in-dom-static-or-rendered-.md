---
step: add_boards
symptom: Careers page shows 0 job listings in DOM (static or rendered). All links are navigation/category pages, not job detail pages.
tags: ['iframe', 'embedded-board', 'zero-jobs', 'dom']
---
# Careers page shows 0 job listings in DOM (static or rendered). All links are navigation/category pages, not job detail pages.

## Problem
Careers page shows 0 job listings in DOM (static or rendered). All links are navigation/category pages, not job detail pages.

## Solution
Inspect the rendered page.html artifact for iframe tags. The actual job listings may be embedded via an iframe pointing to a separate domain (e.g., jobs.company.ch). Use that iframe src as the board URL instead.

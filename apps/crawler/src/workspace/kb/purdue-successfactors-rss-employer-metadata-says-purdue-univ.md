---
step: configure
symptom: Purdue SuccessFactors RSS employer metadata says Purdue University test and feed URLs use numeric job IDs while live listings use requisition-en_US URLs.
tags: ['successfactors', 'rss', 'purdue', 'redirects', 'tenant-verification']
---
# Purdue SuccessFactors RSS employer metadata says Purdue University test and feed URLs use numeric job IDs while live listings use requisition-en_US URLs.

## Problem
Purdue SuccessFactors RSS employer metadata says Purdue University test and feed URLs use numeric job IDs while live listings use requisition-en_US URLs.

## Solution
Do not infer a test or stale tenant from labels or URL format alone. Verify exact feed URLs: sampled Purdue numeric URLs redirected to matching official requisition-en_US pages with substantive identical content. RSS and official sitemap both contained 479 jobs, while the issue inventory was historical (413). The centralized tenant also covers Fort Wayne, Northwest, Purdue Global and PARI; category and campus pages are subsets.

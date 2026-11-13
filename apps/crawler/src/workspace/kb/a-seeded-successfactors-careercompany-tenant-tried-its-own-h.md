---
step: probe_monitor
symptom: A seeded SuccessFactors career?company tenant tried its own host /googlefeed.xml and returned 404, although the company already had a working custom-domain board.
tags: ['rss', 'successfactors', 'inventory', 'redirect', 'duplicates', 'multilingual']
---
# A seeded SuccessFactors career?company tenant tried its own host /googlefeed.xml and returned 404, although the company already had a working custom-domain board.

## Problem
A seeded SuccessFactors career?company tenant tried its own host /googlefeed.xml and returned 404, although the company already had a working custom-domain board.

## Solution
Run normal monitor probing on both URLs rather than retaining or discarding the inventory seed on assumption. For BoehringerPRD, both probes discovered jobs.boehringer-ingelheim.com/googlefeed.xml; live runs and compare-boards proved 898 identical URL/identity records, so keep only the existing custom-domain board. Compare language-specific search totals before treating an all-language feed count larger than default English search as overcollection.

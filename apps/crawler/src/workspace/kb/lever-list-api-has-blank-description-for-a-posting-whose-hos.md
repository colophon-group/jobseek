---
step: select_scraper
symptom: Lever list API has blank description for a posting whose hosted job page still contains full role content.
tags: ['lever', 'json-ld', 'enrichment', 'blank-description']
---
# Lever list API has blank description for a posting whose hosted job page still contains full role content.

## Problem
Lever list API has blank description for a posting whose hosted job page still contains full role content.

## Solution
Inspect both official API and hosted posting before using exclude_blank_posting_ids. On Wing Assistant, hosted static JSON-LD recovered the full HubSpot Executive Assistant description that the list API omitted. Retain native lever monitor and use json-ld scraper with enrich:[description,date_posted] to recover role content and dates while preserving monitor workplace/salary data. Exclude a blank UUID only after confirming its hosted page also has no recoverable duties or requirements; salary-only JSON-LD is not a usable description.

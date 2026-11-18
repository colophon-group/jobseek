---
step: verify_and_feedback
symptom: Modine SuccessFactors RSS returns 401 rich jobs with long numeric job URLs, while indexed detail pages use requisition-locale URLs and JSON-LD extraction returns no fields.
tags: ['successfactors', 'rss', 'redirects', 'json-ld', 'modine', 'verification']
---
# Modine SuccessFactors RSS returns 401 rich jobs with long numeric job URLs, while indexed detail pages use requisition-locale URLs and JSON-LD extraction returns no fields.

## Problem
Modine SuccessFactors RSS returns 401 rich jobs with long numeric job URLs, while indexed detail pages use requisition-locale URLs and JSON-LD extraction returns no fields.

## Solution
Check the saved HTTP response chain and HTML before treating either observation as stale feed evidence. Three Modine sample URLs with numeric IDs redirected 302 to matching requisition-en_US detail URLs with HTTP 200. Their visible role titles, locations and substantive responsibilities/qualifications matched the feed. The independent sitemap also returned 401 job URLs. JSON-LD absence alone did not invalidate the full-data RSS source; keep the validated native RSS config and record optional fields absent when the feed omits them.

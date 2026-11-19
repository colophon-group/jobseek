---
step: configure_scraper
symptom: Gupy inventory seed finds all live jobs but JSON-LD details cover only 8/10 samples; two live roles have no JobPosting JSON-LD.
tags: ['gupy', 'nextdata', 'json-ld', 'partial-coverage', 'description', 'deadline']
---
# Gupy inventory seed finds all live jobs but JSON-LD details cover only 8/10 samples; two live roles have no JobPosting JSON-LD.

## Problem
Gupy inventory seed finds all live jobs but JSON-LD details cover only 8/10 samples; two live roles have no JobPosting JSON-LD.

## Solution
Probe detail scrapers and inspect saved __NEXT_DATA__ at props.pageProps.job. On Petz the missing JSON-LD roles have isJobPostedOnGoogle=false while embedded job data remains complete. A nextdata scraper maps name, addressLine, publishedAt and normalized workplaceType; build description by concatenating description, responsibilities, prerequisites and relevantExperiences. Use registerEndDate for the application deadline: expiresAt can be stale even on published roles. Keep HTML responsibilities/prerequisites inside the HTML description rather than mapping them into plain-text list fields without conversion. Verify all samples and raw enums; the apprentice-only board explicitly states four-hour weekdays, supporting its part_time mapping.

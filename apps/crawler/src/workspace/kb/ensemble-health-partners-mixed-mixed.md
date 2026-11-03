---
type: case-study
company: ensemble-health-partners
monitor: mixed
scraper: mixed
summary: "Workday default all-sites aggregates US main, HSHS and India (516 jobs). Egypt AI Innovation has five Greenhouse jobs requiring entity-decoded HTTP detail descriptions and offices-based locations. India Freshteam has 23 real jobs after referral exclusion: use static DOM links and embedded JobPosting JSON with html_unescape, map FULL_TIME, and extract actual addressCountry only because city/state fields are swapped or false; omit inconsistent remote flag and document non-ISO UTC dates. Freshteam visible full description container is .public_job_description. Full India inventory comparison reveals probable syndicated software roles plus distinct current HRBP, Onboarding and ServiceNow vacancies; ws compare-boards sees only 100 Workday URL samples and cannot establish semantic independence. Existing ensemble is unrelated Ensemble Travel Group, so separate identity is verified."
tags: ['workday', 'greenhouse', 'freshteam', 'multi-ats', 'html-unescape', 'location-quality', 'partial-overlap']
---
# Ensemble-Health-Partners — Workday default all-sites aggregates US main, HSHS and India (516 jobs). Egypt AI Innovation has five Greenhouse jobs requiring entity-decoded HTTP detail descriptions and offices-based locations. India Freshteam has 23 real jobs after referral exclusion: use static DOM links and embedded JobPosting JSON with html_unescape, map FULL_TIME, and extract actual addressCountry only because city/state fields are swapped or false; omit inconsistent remote flag and document non-ISO UTC dates. Freshteam visible full description container is .public_job_description. Full India inventory comparison reveals probable syndicated software roles plus distinct current HRBP, Onboarding and ServiceNow vacancies; ws compare-boards sees only 100 Workday URL samples and cannot establish semantic independence. Existing ensemble is unrelated Ensemble Travel Group, so separate identity is verified.

## Setup
- Monitor: mixed
- Scraper: mixed

## Key decisions
<!-- What was non-obvious? What was tried? What worked? -->

## Config
<!-- Final scraper_config / monitor_config JSON -->

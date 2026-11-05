---
step: scraper
symptom: Centerstone Workday JSON-LD marks Office Based and Hybrid jobs as TELECOMMUTE and exposes internal office labels as addresses
tags: ['workday', 'json-ld', 'job_location_type', 'centerstone']
---
# Centerstone Workday JSON-LD marks Office Based and Hybrid jobs as TELECOMMUTE and exposes internal office labels as addresses

## Problem
Centerstone Workday JSON-LD marks Office Based and Hybrid jobs as TELECOMMUTE and exposes internal office labels as addresses

## Solution
Compare Workday API scraper against JSON-LD on the same jobs before switching for greater field coverage. For centerstone.wd5/CenterstoneCareers, dedicated workday produced real city/state locations and did not falsely mark Office Based or Hybrid roles remote. JSON-LD showed TELECOMMUTE on Assessment Counselor JR-033006 and Philanthropy Manager JR-033682, with internal office labels. Retain dedicated workday and document unsupported optional remoteType labels; do not infer remote from non-empty JSON-LD alone.

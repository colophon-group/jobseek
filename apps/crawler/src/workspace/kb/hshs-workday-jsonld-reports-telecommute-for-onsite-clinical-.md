---
step: scraper
symptom: HSHS Workday JSON-LD reports TELECOMMUTE for onsite clinical roles despite complete field coverage.
tags: ['workday', 'json-ld', 'hshs', 'job-location-type']
---
# HSHS Workday JSON-LD reports TELECOMMUTE for onsite clinical roles despite complete field coverage.

## Problem
HSHS Workday JSON-LD reports TELECOMMUTE for onsite clinical roles despite complete field coverage.

## Solution
Compare the same requisitions through the native Workday detail scraper. On HSHS R1038267, R1032141 and R1040264, native details returned onsite with clean city/state while JSON-LD returned TELECOMMUTE and facility-based address labels. JSON-LD added no structured salary, so retain the native workday scraper rather than choosing by field presence alone.

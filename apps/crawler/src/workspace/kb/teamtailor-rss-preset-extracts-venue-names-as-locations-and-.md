---
step: test_config
symptom: Teamtailor RSS preset extracts venue names as locations and may look complete despite lacking city names.
tags: ['teamtailor', 'rss', 'locations', 'json-ld']
---
# Teamtailor RSS preset extracts venue names as locations and may look complete despite lacking city names.

## Problem
Teamtailor RSS preset extracts venue names as locations and may look complete despite lacking city names.

## Solution
Compare extracted locations with live job details. On Leitmotiv, generic RSS enumerated all 10 jobs without misleading venue locations, and JSON-LD detail scraping supplied real Paris, Locquirec and Rome locations.

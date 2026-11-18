---
step: configure_boards
symptom: Teamtailor jobs.rss returns complete descriptions and dates but 0/N locations when the tenant labels each listing only with its property name.
tags: ['teamtailor', 'rich-rows', 'rss', 'missing-location']
---
# Teamtailor jobs.rss returns complete descriptions and dates but 0/N locations when the tenant labels each listing only with its property name.

## Problem
Teamtailor jobs.rss returns complete descriptions and dates but 0/N locations when the tenant labels each listing only with its property name.

## Solution
Use a static DOM rich_rows monitor on the unfiltered /jobs page with the job anchors as row_selector and default_locations set only after verifying every listing is at one place; pair with JSON-LD detail enrichment for descriptions. This preserves 100% titles, locations and descriptions while new postings inherit the verified location.

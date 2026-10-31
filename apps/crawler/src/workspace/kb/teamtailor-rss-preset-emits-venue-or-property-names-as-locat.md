---
step: test_config
symptom: Teamtailor RSS preset emits venue or property names as locations despite structured city and country tags in the feed.
tags: ['teamtailor', 'rss', 'json-ld', 'locations']
---
# Teamtailor RSS preset emits venue or property names as locations despite structured city and country tags in the feed.

## Problem
Teamtailor RSS preset emits venue or property names as locations despite structured city and country tags in the feed.

## Solution
Select the generic RSS preset for full unfiltered /jobs.rss coverage, then use the JSON-LD detail scraper to obtain city, region, country and employment type. Verify count against /jobs and sample detail quality.

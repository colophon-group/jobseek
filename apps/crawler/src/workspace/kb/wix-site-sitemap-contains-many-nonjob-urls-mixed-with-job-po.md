---
step: select_monitor
symptom: Wix site sitemap contains many non-job URLs mixed with job postings
tags: ['wix', 'sitemap', 'url_filter']
---
# Wix site sitemap contains many non-job URLs mixed with job postings

## Problem
Wix site sitemap contains many non-job URLs mixed with job postings

## Solution
Use url_filter regex in sitemap monitor config to match only job-related URL patterns (e.g. stellenanzeige-). Check pages-sitemap.xml to identify job URL patterns.

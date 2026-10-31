---
step: configure
symptom: api_sniffer auto-mapped Taleez /api/careez job URL to a shared logo image and collapsed a 31-job board to six image URLs; descriptions were empty.
tags: ['taleez', 'api_sniffer', 'url_template', 'job-urls']
---
# api_sniffer auto-mapped Taleez /api/careez job URL to a shared logo image and collapsed a 31-job board to six image URLs; descriptions were empty.

## Problem
api_sniffer auto-mapped Taleez /api/careez job URL to a shared logo image and collapsed a 31-job board to six image URLs; descriptions were empty.

## Solution
Inspect the public jobs array. Use api_url /api/careez, json_path jobs, and url_template https://taleez.com/apply/{slug}; map only title and locations, then pair with json-ld detail scraping. Verify the full API count and sample detail pages.

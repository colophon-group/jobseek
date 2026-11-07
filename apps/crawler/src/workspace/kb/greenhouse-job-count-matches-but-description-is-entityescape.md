---
step: configure
symptom: Greenhouse job count matches, but description is entity-escaped HTML, offices add non-geographic location labels, and exposed Full Time / Part Time metadata is missing.
tags: ['greenhouse', 'api_sniffer', 'html_unescape', 'locations', 'employment_type']
---
# Greenhouse job count matches, but description is entity-escaped HTML, offices add non-geographic location labels, and exposed Full Time / Part Time metadata is missing.

## Problem
Greenhouse job count matches, but description is entity-escaped HTML, offices add non-geographic location labels, and exposed Full Time / Part Time metadata is missing.

## Solution
Inspect response.json and jobs.json before rating quality. On DH Pace, a named greenhouse fields override was ignored. Configure api_sniffer direct HTTP replay of https://boards-api.greenhouse.io/v1/boards/dhpace/jobs?content=true with json_path jobs, url_field absolute_url, total_path meta.total; map description with path content plus html_unescape true, locations from location.name, date_posted from first_published and employment_type from metadata[0].value with Full Time/Part Time normalization after verifying metadata shape across all rows. Retain office names in metadata instead of locations. Verify API/public board counts and full role samples; no browser required.

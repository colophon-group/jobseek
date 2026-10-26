---
step: select_monitor
symptom: Jobsyn automatic API probe ranks filters.city facets as jobs and constructing detail URL slugs from sparse city fields changes canonical URLs.
tags: ['sitemap', 'jobsyn', 'api_sniffer', 'canonical-url']
---
# Jobsyn automatic API probe ranks filters.city facets as jobs and constructing detail URL slugs from sparse city fields changes canonical URLs.

## Problem
Jobsyn automatic API probe ranks filters.city facets as jobs and constructing detail URL slugs from sparse city fields changes canonical URLs.

## Solution
Use verified official sitemap URLs for canonical identities. For EPES drivers, proxy-backed sitemap returns356 jobs. The public prod-search-api.jobsyn.org/api/v1/solr/search supports exact q=guid:GUID with x-origin and referer. api_sniffer HTTP detail mapping via url_pattern GUID and json_path jobs[0] supplies title_exact, location_exact, date_new and complete description wrapped in HTML. Verify distinct title/location samples and compare with real detail content; avoid treating facets or guessed slugs as jobs.

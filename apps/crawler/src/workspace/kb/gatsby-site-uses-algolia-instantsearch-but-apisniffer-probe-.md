---
step: Select and test monitor
symptom: Gatsby site uses Algolia InstantSearch but api_sniffer probe only captures Gatsby page-data.json, missing the actual Algolia search API calls
tags: ['algolia', 'gatsby', 'api-sniffer', 'js-bundle']
---
# Gatsby site uses Algolia InstantSearch but api_sniffer probe only captures Gatsby page-data.json, missing the actual Algolia search API calls

## Problem
Gatsby site uses Algolia InstantSearch but api_sniffer probe only captures Gatsby page-data.json, missing the actual Algolia search API calls

## Solution
Find Algolia App ID and API key in JS bundle files (search for appId pattern in page-specific chunks like component---src-pages-en-search-tsx-*.js). Then use Algolia REST API to list all indexes (GET /1/indexes) and identify the jobs index. Configure api_sniffer directly against the Algolia REST endpoint with X-Algolia headers as query params.

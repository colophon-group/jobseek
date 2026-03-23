---
step: select_monitor
symptom: API sniffer captures internal API but API response lacks location data
tags: ['api_sniffer', 'json-ld', 'locations', 'url-only']
---
# API sniffer captures internal API but API response lacks location data

## Problem
API sniffer captures internal API but API response lacks location data

## Solution
Use api_sniffer in URL-only mode (remove fields from config) paired with json-ld scraper to extract locations from individual job pages

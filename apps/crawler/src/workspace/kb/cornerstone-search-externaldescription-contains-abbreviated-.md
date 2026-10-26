---
step: select_scraper
symptom: Cornerstone search externalDescription contains abbreviated text, placeholders or literal ellipsis, while rich monitor auto-selects skip.
tags: ['cornerstone', 'description', 'api_sniffer', 'enrichment']
---
# Cornerstone search externalDescription contains abbreviated text, placeholders or literal ellipsis, while rich monitor auto-selects skip.

## Problem
Cornerstone search externalDescription contains abbreviated text, placeholders or literal ellipsis, while rich monitor auto-selects skip.

## Solution
Compare rendered posting with the anonymous detail responses. Penske ANZ complete HTML is data[0].items[0].fields.ad from Services/API/ATS/CareerSite/1/JobRequisitions/ID. Existing browser api_sniffer with json_path data[0].items[0].fields.{title:title,description:ad,locations:location}, fields description:description, and enrich:[description] recovers full ads. Explicit projection improves single-job selection over generic heuristics; direct anonymous HTTP401, browser required. Preserve monitor city/state/country locations. Verify full samples and resource-policy parity.

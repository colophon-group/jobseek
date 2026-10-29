---
type: case-study
company: university-of-texas-at-austin
monitor: api_sniffer
scraper: api_sniffer
summary: "UT Austin faculty: validate all 178 Algolia IDs against rendered pagination and official assets/ids.json. Search records omit qualifications and ISO dates; public assets/{id}.json returns a one-object array with description, qualifications, instructions, open_date_iso and close_date_iso. Use JSON path [0] and concatenate all three HTML sections. Request attributesToRetrieve=[objectID] in the unfiltered Algolia search to avoid automatic rich-field inference and enable a full detail scraper. Rich enrichment rejects valid_through in CSV validation, while normal detail scraping preserves it in extras. Normalize observed campus location labels while retaining non-Austin cities. No browser or runtime code changes are needed for faculty collection."
tags: ['algolia', 'faculty', 'api-sniffer', 'complete-descriptions', 'url-only', 'deadlines']
---
# University-Of-Texas-At-Austin — UT Austin faculty: validate all 178 Algolia IDs against rendered pagination and official assets/ids.json. Search records omit qualifications and ISO dates; public assets/{id}.json returns a one-object array with description, qualifications, instructions, open_date_iso and close_date_iso. Use JSON path [0] and concatenate all three HTML sections. Request attributesToRetrieve=[objectID] in the unfiltered Algolia search to avoid automatic rich-field inference and enable a full detail scraper. Rich enrichment rejects valid_through in CSV validation, while normal detail scraping preserves it in extras. Normalize observed campus location labels while retaining non-Austin cities. No browser or runtime code changes are needed for faculty collection.

## Setup
- Monitor: api_sniffer
- Scraper: api_sniffer

## Key decisions
<!-- What was non-obvious? What was tried? What worked? -->

## Config
<!-- Final scraper_config / monitor_config JSON -->

---
type: case-study
company: zimmer-biomet
monitor: phenom
scraper: embedded
summary: "Phenom sitemap produced 410 global job URLs. Default JSON-LD had entity-escaped description HTML, OTHER employment type and no workplace mode. Embedded phApp.ddo jobDetail.data.job exposes full HTML description, hiringType, jobClassification, postedDate and multi_location. Map hiringType and jobClassification to canonical enums. Japanese raw locations contain numeric prefixes, while standardized regions can be wrong (Kanagawa labeled Fukushima); retain Japanese standardized place and country names while preserving raw full multi-locations elsewhere. Tested 10 regular and 5 targeted samples with clean required fields and six-field coverage; regional portals and Paragon 28 centralize on this board, root inventory URL is a mirror."
tags: ['phenom', 'embedded', 'json-ld', 'locations', 'employment-type']
---
# Zimmer-Biomet — Phenom sitemap produced 410 global job URLs. Default JSON-LD had entity-escaped description HTML, OTHER employment type and no workplace mode. Embedded phApp.ddo jobDetail.data.job exposes full HTML description, hiringType, jobClassification, postedDate and multi_location. Map hiringType and jobClassification to canonical enums. Japanese raw locations contain numeric prefixes, while standardized regions can be wrong (Kanagawa labeled Fukushima); retain Japanese standardized place and country names while preserving raw full multi-locations elsewhere. Tested 10 regular and 5 targeted samples with clean required fields and six-field coverage; regional portals and Paragon 28 centralize on this board, root inventory URL is a mirror.

## Setup
- Monitor: phenom
- Scraper: embedded

## Key decisions
<!-- What was non-obvious? What was tried? What worked? -->

## Config
<!-- Final scraper_config / monitor_config JSON -->

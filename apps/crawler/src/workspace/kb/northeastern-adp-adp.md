---
type: case-study
company: northeastern
monitor: adp
scraper: adp
summary: "Northeastern uses global Workday aggregation including GBR_NULondonCareers plus a distinct NYC ADP board after the Marymount merger. Native ADP detail extracts full HTML and salary but seven of ten rows omit structured locations; a missing-only New York fallback was accepted only after checking every current external job ID against employer descriptions. Some aggregators mislabeled these roles Portland and an older Oakland role reused a title, so exact apply IDs and employer prose were necessary. Workday JSON-LD added no fields and had less precise locations than the native detail API."
tags: ['adp', 'workday', 'multi-board', 'missing-location', 'university', 'nyc']
---
# Northeastern — Northeastern uses global Workday aggregation including GBR_NULondonCareers plus a distinct NYC ADP board after the Marymount merger. Native ADP detail extracts full HTML and salary but seven of ten rows omit structured locations; a missing-only New York fallback was accepted only after checking every current external job ID against employer descriptions. Some aggregators mislabeled these roles Portland and an older Oakland role reused a title, so exact apply IDs and employer prose were necessary. Workday JSON-LD added no fields and had less precise locations than the native detail API.

## Setup
- Monitor: adp
- Scraper: adp

## Key decisions
<!-- What was non-obvious? What was tried? What worked? -->

## Config
<!-- Final scraper_config / monitor_config JSON -->

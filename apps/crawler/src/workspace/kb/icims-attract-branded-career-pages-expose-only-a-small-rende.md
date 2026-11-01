---
step: test_config
symptom: iCIMS Attract branded career pages expose only a small rendered first page, while the site API has separate brand-tagged inventories and full job fields.
tags: ['icims', 'api_sniffer', 'brands', 'pagination', 'rich-monitor']
---
# iCIMS Attract branded career pages expose only a small rendered first page, while the site API has separate brand-tagged inventories and full job fields.

## Problem
iCIMS Attract branded career pages expose only a small rendered first page, while the site API has separate brand-tagged inventories and full job fields.

## Solution
Probe each brand path, then replay its public /api/jobs endpoint with the captured tags1 brand parameter and page pagination. Map nested data.title, data.description, data.full_location, employment_type and posted_date for a fast rich monitor; wrap plain description text in an HTML paragraph and compare brand boards for overlap.

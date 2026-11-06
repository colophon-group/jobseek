---
step: select_scraper
symptom: Hitachi Energy JSON-LD descriptions contain generic apply-today copy despite full field coverage; careers sitemap also contains five campaign landing pages.
tags: ['hitachi-energy', 'sitemap', 'json-ld', 'description_selector', 'boilerplate']
---
# Hitachi Energy JSON-LD descriptions contain generic apply-today copy despite full field coverage; careers sitemap also contains five campaign landing pages.

## Problem
Hitachi Energy JSON-LD descriptions contain generic apply-today copy despite full field coverage; careers sitemap also contains five campaign landing pages.

## Solution
Use sitemap_url https://www.hitachienergy.com/careers/open-jobs/sitemap.xml with url_filter /careers/open-jobs/details/. Pair json-ld scraper with description_selector .cmp-job-details__description-block to require the full static role body. Verified three full role descriptions, clean locations, employment types and dates; 2232 details vs current API total2234. Do not accept nonempty boilerplate as a substantive description.

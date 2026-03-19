---
step: select_scraper
symptom: DOM monitor on Phenom People (phenompeople.com) ATS only captures first page of search results. Pagination uses offset parameter (from=10, from=20) not standard page parameter. Also captures non-job URLs (category pages, SuccessFactors application links).
tags: ['phenom', 'phenompeople', 'sitemap', 'json-ld', 'successfactors', 'pagination']
---
# DOM monitor on Phenom People (phenompeople.com) ATS only captures first page of search results. Pagination uses offset parameter (from=10, from=20) not standard page parameter. Also captures non-job URLs (category pages, SuccessFactors application links).

## Problem
DOM monitor on Phenom People (phenompeople.com) ATS only captures first page of search results. Pagination uses offset parameter (from=10, from=20) not standard page parameter. Also captures non-job URLs (category pages, SuccessFactors application links).

## Solution
Use sitemap monitor with url_filter for the job path (e.g., /ch/de/job/). Sitemap provides complete coverage. Pair with json-ld scraper — Phenom People job detail pages have schema.org JobPosting markup. Note: SuccessFactors application URLs (career5.successfactors.eu) do NOT have json-ld.

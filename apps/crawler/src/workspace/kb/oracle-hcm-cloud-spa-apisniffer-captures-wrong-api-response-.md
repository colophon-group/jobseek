---
step: select_monitor
symptom: "Oracle HCM Cloud SPA: api_sniffer captures wrong API response (page template metadata instead of job data), json-ld not present, all scrapers fail on static HTML"
tags: ['oracle-hcm', 'spa', 'dom-scraper', 'render', 'sitemap']
---
# Oracle HCM Cloud SPA: api_sniffer captures wrong API response (page template metadata instead of job data), json-ld not present, all scrapers fail on static HTML

## Problem
Oracle HCM Cloud SPA: api_sniffer captures wrong API response (page template metadata instead of job data), json-ld not present, all scrapers fail on static HTML

## Solution
Use sitemap monitor with url_filter for job URLs. For scraping, use dom scraper with render:true and a wait action (15s). The SPA renders job content into h1.job-details__title (title), div.job-details__subtitle (locations), li.job-meta__item (metadata), and h2.job-details__description-header sections (description). Static HTML has og:title and og:description meta tags but dom scraper cannot extract from meta tag attributes.

---
step: add_boards
symptom: Main careers site is a JS-rendered SPA that times out, but a .dejobs.org mirror exists with a crawlable sitemap
tags: ['sitemap', 'dejobs', 'js-spa', 'alternative-board']
---
# Main careers site is a JS-rendered SPA that times out, but a .dejobs.org mirror exists with a crawlable sitemap

## Problem
Main careers site is a JS-rendered SPA that times out, but a .dejobs.org mirror exists with a crawlable sitemap

## Solution
Some large companies have their main careers on a JS-heavy SPA (e.g. ibm.com/careers/search) but also publish jobs via dejobs.org subdomains which are Nuxt.js SPAs with XML sitemaps. Check sitemap.xml on the dejobs.org domain - it may contain all job URLs. Use sitemap monitor with url_filter to extract only job pages.

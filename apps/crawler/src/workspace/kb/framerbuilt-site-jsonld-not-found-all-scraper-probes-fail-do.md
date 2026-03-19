---
step: select_scraper
symptom: "Framer-built site: json-ld not found, all scraper probes fail, DOM elements not present in static HTML"
tags: ['framer', 'js-rendered', 'dom-scraper', 'render']
---
# Framer-built site: json-ld not found, all scraper probes fail, DOM elements not present in static HTML

## Problem
Framer-built site: json-ld not found, all scraper probes fail, DOM elements not present in static HTML

## Solution
Use dom scraper with render:true, wait:networkidle, and actions:[wait 3000ms]. Framer renders all content via JS. Use h2/h6 tags for title/location. Use 'Apply Now' button text as anchor to locate description section, then collect p elements with stop at second 'Apply Now'. Also check framer-search-index meta tag for a static JSON with all page content.

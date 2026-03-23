---
step: select_scraper
symptom: "DOM scraper title extraction picks wrong h1 element despite attr filter"
tags: [dom-scraper, h1, title, attr, selector]
---
# DOM scraper title extraction picks wrong h1

## Problem
When a job page has multiple h1 elements (e.g., one with `class="slogan"` and
one with `class="job-title"`), the DOM scraper's attr filter may not
disambiguate correctly. The scraper picks the first matching element, which
might be a page slogan, company name, or header text instead of the actual
job title. This produces the same incorrect title across all sample pages.

## Solution
Use a more specific selector to target the correct title element.

1. Inspect `flat.json` in the scraper probe artifacts to see the DOM element
   order and identify which element contains the actual job title.

2. Try a more specific tag or attribute combination:
   ```bash
   # If the job title uses h2 instead of h1
   ws select scraper dom --config '{"steps": [{"field": "title", "tag": "h2", "attr": {"class": "job-title"}}]}'

   # Or use a range/offset to skip the first h1
   ws select scraper dom --config '{"steps": [{"field": "title", "tag": "h1", "range": [1, 2]}]}'
   ```

3. Verify across multiple sample pages using `ws run scraper` — the wrong
   title will be consistent across all samples (same slogan text repeated),
   while the correct title will vary per job.

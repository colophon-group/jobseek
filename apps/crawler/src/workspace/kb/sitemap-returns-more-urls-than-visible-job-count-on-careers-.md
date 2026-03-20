---
step: add_boards
symptom: Sitemap returns more URLs than visible job count on careers page
tags: ['sitemap', 'url_filter', 'non-job-urls']
---
# Sitemap returns more URLs than visible job count on careers page

## Problem
Sitemap returns more URLs than visible job count on careers page

## Solution
Apply url_filter matching the job URL path segment (e.g. '/job/') to exclude non-job pages (about, blog, category pages). Filtered count should match the site's displayed total.

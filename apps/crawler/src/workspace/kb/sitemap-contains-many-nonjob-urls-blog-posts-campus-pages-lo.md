---
step: select_monitor
symptom: Sitemap contains many non-job URLs (blog posts, campus pages, location pages) mixed with real job URLs
tags: ['sitemap', 'url_filter', 'non-job-urls', 'phenom']
---
# Sitemap contains many non-job URLs (blog posts, campus pages, location pages) mixed with real job URLs

## Problem
Sitemap contains many non-job URLs (blog posts, campus pages, location pages) mixed with real job URLs

## Solution
Add url_filter to sitemap monitor config to filter by job URL path pattern (e.g., '/global/en/job/'). Compare filtered count against unfiltered to verify coverage. In this case 557/1449 URLs were non-job pages.

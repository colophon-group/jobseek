---
step: select_monitor
symptom: DOM monitor finds excessive job links (38 vs 6 expected) because careers page contains taxonomy filter links with query parameters like ?query-3605-taxQuery-job-must-have
tags: ['dom', 'url_filter', 'false-positives', 'taxonomy-links']
---
# DOM monitor finds excessive job links (38 vs 6 expected) because careers page contains taxonomy filter links with query parameters like ?query-3605-taxQuery-job-must-have

## Problem
DOM monitor finds excessive job links (38 vs 6 expected) because careers page contains taxonomy filter links with query parameters like ?query-3605-taxQuery-job-must-have

## Solution
Apply url_filter to DOM monitor config to match only actual job detail paths, e.g. url_filter: '/careers/[a-z]' to exclude query-string filter/taxonomy links

---
step: select_monitor
symptom: SuccessFactors viewalljobs page showed only 29 jobs but googlefeed.xml contained 262 jobs
tags: ['successfactors', 'rss', 'job-count', 'superset']
---
# SuccessFactors viewalljobs page showed only 29 jobs but googlefeed.xml contained 262 jobs

## Problem
SuccessFactors viewalljobs page showed only 29 jobs but googlefeed.xml contained 262 jobs

## Solution
Always check for googlefeed.xml on SuccessFactors boards — the HTML page may show a small subset while the RSS feed contains the full job list. Use rss monitor with successfactors preset instead of dom monitor for complete coverage.

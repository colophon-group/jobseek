---
step: select_monitor
symptom: SmartRecruiters detected by probe but API returns 0 jobs
tags: ['smartrecruiters', 'rss', 'successfactors', 'zero-jobs']
---
# SmartRecruiters detected by probe but API returns 0 jobs

## Problem
SmartRecruiters detected by probe but API returns 0 jobs

## Solution
Fall back to RSS monitor with SuccessFactors preset. The googlefeed.xml endpoint (RSS) often provides full coverage when the SmartRecruiters API fails. Check probe output for rss/successfactors detection.

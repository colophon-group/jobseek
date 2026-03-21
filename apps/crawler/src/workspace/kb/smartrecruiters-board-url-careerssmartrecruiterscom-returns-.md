---
step: add_boards
symptom: SmartRecruiters board URL (careers.smartrecruiters.com) returns 0 job postings despite company having hundreds of open roles
tags: ['smartrecruiters', 'workday', 'ats-migration', 'zero-jobs']
---
# SmartRecruiters board URL (careers.smartrecruiters.com) returns 0 job postings despite company having hundreds of open roles

## Problem
SmartRecruiters board URL (careers.smartrecruiters.com) returns 0 job postings despite company having hundreds of open roles

## Solution
Company migrated ATS. Fetch the careers-search/job-search page on the company website and look for outgoing links or references to the actual ATS (in this case Workday at *.wd3.myworkdayjobs.com). The FAQ page mentioning 'Workday' was also a clue.

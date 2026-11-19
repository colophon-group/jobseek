---
step: configure
symptom: UofL Health Workday inventory seed default all-sites aggregation returned only 87 jobs even though sites_total was 1 and public careers frontend showed about 536.
tags: ['workday', 'job-count', 'all-sites', 'single-site', 'uofl-health']
---
# UofL Health Workday inventory seed default all-sites aggregation returned only 87 jobs even though sites_total was 1 and public careers frontend showed about 536.

## Problem
UofL Health Workday inventory seed default all-sites aggregation returned only 87 jobs even though sites_total was 1 and public careers frontend showed about 536.

## Solution
Verify official links and every alternate board first. For this verified single-site tenant, exact official site UofLHealthCareers with all_sites=false returned 539 jobs and dedicated Workday scraper passed 10/10 required-field samples. Do not accept the aggregated count or disable multi-site coverage on other employers without independent board-completeness evidence; aggregation cause was not diagnosed.

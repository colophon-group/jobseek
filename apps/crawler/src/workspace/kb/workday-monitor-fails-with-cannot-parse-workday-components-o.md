---
step: select_monitor
symptom: Workday monitor fails with 'Cannot parse Workday components' on custom career domains (e.g. careers.company.com) that proxy to Workday (company.wd1.myworkdayjobs.com)
tags: ['workday', 'custom-domain', 'sitemap', 'json-ld']
---
# Workday monitor fails with 'Cannot parse Workday components' on custom career domains (e.g. careers.company.com) that proxy to Workday (company.wd1.myworkdayjobs.com)

## Problem
Workday monitor fails with 'Cannot parse Workday components' on custom career domains (e.g. careers.company.com) that proxy to Workday (company.wd1.myworkdayjobs.com)

## Solution
Use sitemap monitor with url_filter instead. The custom domain's sitemap typically lists all job URLs. Combine with json-ld scraper which extracts structured JobPosting data from the Workday-rendered pages.

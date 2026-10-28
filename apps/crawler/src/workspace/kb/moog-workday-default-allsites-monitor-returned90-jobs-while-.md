---
step: configure
symptom: Moog Workday default all-sites monitor returned90 jobs while native probe and official sitemap both reported454; canonical site-token casing did not change the result.
tags: ['workday', 'moog', 'incomplete-count', 'all-sites', 'single-site']
---
# Moog Workday default all-sites monitor returned90 jobs while native probe and official sitemap both reported454; canonical site-token casing did not change the result.

## Problem
Moog Workday default all-sites monitor returned90 jobs while native probe and official sitemap both reported454; canonical site-token casing did not change the result.

## Solution
Verify all regional career links converge on the single global MOOG_External_Career_Site, then configure company=moog, wd_instance=wd5, site=MOOG_External_Career_Site, all_sites=false. This returned454 jobs and10/10 clean detail samples. Reject incomplete default configs. This is a bounded single-site workaround; do not apply to tenants with multiple distinct official sites. Compare native all-sites aggregation against source totals before acceptance.

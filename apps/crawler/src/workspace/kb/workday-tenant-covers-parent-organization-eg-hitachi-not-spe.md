---
step: select_monitor
symptom: Workday tenant covers parent organization (e.g. Hitachi) not specific subsidiary (e.g. Hitachi Energy). 3619 mixed jobs vs 799 subsidiary-specific jobs on company careers page.
tags: ['workday', 'subsidiary', 'parent-org', 'api_sniffer']
---
# Workday tenant covers parent organization (e.g. Hitachi) not specific subsidiary (e.g. Hitachi Energy). 3619 mixed jobs vs 799 subsidiary-specific jobs on company careers page.

## Problem
Workday tenant covers parent organization (e.g. Hitachi) not specific subsidiary (e.g. Hitachi Energy). 3619 mixed jobs vs 799 subsidiary-specific jobs on company careers page.

## Solution
When a Workday board belongs to a parent org and no subsidiary-specific Workday sub-site exists, prefer the subsidiary's own careers page with api_sniffer or dom monitor for subsidiary-specific job listings.

---
step: select_monitor
symptom: Workday multi-site discovery shows intermittent 500 errors on some sites while others work
tags: ['workday', 'multi-site', '500-error', 'intermittent']
---
# Workday multi-site discovery shows intermittent 500 errors on some sites while others work

## Problem
Workday multi-site discovery shows intermittent 500 errors on some sites while others work

## Solution
Workday tenants can have multiple job sites (discovered via robots.txt). Some sites may intermittently return 500 errors. As long as at least one site returns jobs with correct count, the monitor is working. The all_sites:true default handles failover across sites.

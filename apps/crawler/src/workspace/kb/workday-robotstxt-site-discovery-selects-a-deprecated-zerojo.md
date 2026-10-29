---
step: add_boards
symptom: Workday robots.txt site discovery selects a deprecated zero-job site although the confirmed official tenant site has hundreds of active jobs
tags: ['workday', 'robots', 'zero-jobs', 'multi-site']
---
# Workday robots.txt site discovery selects a deprecated zero-job site although the confirmed official tenant site has hundreds of active jobs

## Problem
Workday robots.txt site discovery selects a deprecated zero-job site although the confirmed official tenant site has hundreds of active jobs

## Solution
Compare the official careers application target and a normal Workday probe with the monitor result. MGM Resorts mgmcareers had 481 live jobs while robots discovery selected Pool-Hiring-2021 with zero. After checking all advertised sites, select the verified actual site directly or an explicit ordered sites list; do not accept the zero result or assume inventory validates scope.

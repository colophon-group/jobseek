---
step: select_monitor
symptom: Aggregator company pages can expose many generic /jobs taxonomy links, causing DOM monitor overcount and misleading inferred patterns.
tags: ['dom-monitor', 'job-link-pattern', 'aggregator', 'overcount']
---
# Aggregator company pages can expose many generic /jobs taxonomy links, causing DOM monitor overcount and misleading inferred patterns.

## Problem
Aggregator company pages can expose many generic /jobs taxonomy links, causing DOM monitor overcount and misleading inferred patterns.

## Solution
Use render=true and constrain discovery with a company-specific URL regex (e.g., '-at-<company>(?:-[0-9]+)?'), then verify discovered URLs are true job-detail pages.

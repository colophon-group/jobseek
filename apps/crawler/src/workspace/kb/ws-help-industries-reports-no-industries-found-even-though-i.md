---
step: setup
symptom: ws help industries reports no industries found even though industries.csv exists
tags: ['taxonomy', 'industries', 'help', 'path-resolution', 'legacy-schema']
---
# ws help industries reports no industries found even though industries.csv exists

## Problem
ws help industries reports no industries found even though industries.csv exists

## Solution
Use repo-root-aware data lookup in ws help and taxonomy commands, and render legacy industries.csv rows that still use the name column.

---
step: configure
symptom: Workday robots-based all-sites aggregation can include independent agency employers while omitting an official sourcing site.
tags: ['workday', 'multi-site', 'scope', 'internships', 'allstate']
---
# Workday robots-based all-sites aggregation can include independent agency employers while omitting an official sourcing site.

## Problem
Workday robots-based all-sites aggregation can include independent agency employers while omitting an official sourcing site.

## Solution
Verify each site employer and its official careers provenance. For Allstate, the official licensed-sales-professionals disclaimer says Agent staff work for independent agencies, whereas sourcing_event contains Allstate and Arity internships absent from robots discovery. Use the exact ordered sites list [allstate_careers,sourcing_event] to retain 456 employer roles and exclude Agent. Check each site live and compare the union; do not assume every site under a tenant has the same employer scope.

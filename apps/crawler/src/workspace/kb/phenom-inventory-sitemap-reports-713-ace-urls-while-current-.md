---
step: configure
symptom: Phenom inventory sitemap reports 713 ACE URLs while current Workday tenant has 480 jobs; compare-boards reports no overlap because domains differ.
tags: ['phenom', 'workday', 'mirrors', 'stale-sitemap', 'overlap']
---
# Phenom inventory sitemap reports 713 ACE URLs while current Workday tenant has 480 jobs; compare-boards reports no overlap because domains differ.

## Problem
Phenom inventory sitemap reports 713 ACE URLs while current Workday tenant has 480 jobs; compare-boards reports no overlap because domains differ.

## Solution
Treat cross-domain URL disjointness as inconclusive. Verify shared requisition IDs directly: ACE Sales Agent JR202638774 and Retail Service Specialist JR202639254 match across Phenom and Workday. Phenom random sample included two HTTP 410 jobs and escaped description HTML; retain native Workday live API and remove redundant stale sitemap source after verifying regional and affiliate coverage.

---
step: board_research
symptom: Rosewood and New World Avature branded portals each advertise 809 jobs and ws compare-boards reports zero shared URLs
tags: ['avature', 'mirror', 'branded-portal', 'location', 'dom']
---
# Rosewood and New World Avature branded portals each advertise 809 jobs and ws compare-boards reports zero shared URLs

## Problem
Rosewood and New World Avature branded portals each advertise 809 jobs and ws compare-boards reports zero shared URLs

## Solution
The URL prefixes differ by brand but the numeric role IDs match 100/100 in sampled monitor artifacts. Treat them as mirrored portal views and keep one unfiltered global board to avoid duplicate jobs. Rosewood job JSON-LD omits location for many roles; static DOM page title and General Information contain city-country, and the documented DOM_GO_PARSE_ENABLED=0 local verification fallback works when /usr/local/bin/dom-detail-parse is absent.

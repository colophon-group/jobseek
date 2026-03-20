---
step: add_boards
symptom: Personio blind probe matched wrong company (slug collision: hpe.jobs.personio.de belonged to Hans Peter Esser GmbH, not Hewlett Packard Enterprise)
tags: ['personio', 'blind-probe', 'false-positive', 'slug-collision']
---
# Personio blind probe matched wrong company (slug collision: hpe.jobs.personio.de belonged to Hans Peter Esser GmbH, not Hewlett Packard Enterprise)

## Problem
Personio blind probe matched wrong company (slug collision: hpe.jobs.personio.de belonged to Hans Peter Esser GmbH, not Hewlett Packard Enterprise)

## Solution
Always verify blind probe results by checking the actual company name on the page. Low-score blind probes (0.12) with common abbreviation slugs are especially likely to be false positives.

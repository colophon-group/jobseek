---
step: select_monitor
symptom: "Rippling blind probe matches wrong company due to slug collision"
tags: [rippling, blind-probe, false-positive, slug-collision]
---
# Rippling blind probe matches wrong company due to slug collision

## Problem
Rippling's API uses short slugs (e.g., "gs", "ab") that can match unrelated
companies. The blind probe may return jobs from a completely different company
— for example, warehouse/porter roles for a financial services company. The
probe typically returns a small number of irrelevant jobs with titles and
locations that don't match the target company's industry or geography.

## Solution
Always verify blind probe results by checking job titles and locations against
the target company's known industry and operating regions.

1. If jobs are clearly unrelated (e.g., warehouse/porter roles for a financial
   services company, or restaurant jobs for a software company), discard the
   Rippling board entirely.

2. Record the verdict as unusable with a note about the slug collision:
   ```bash
   ws feedback --verdict bad --reason "Rippling slug collision — jobs belong to unrelated company"
   ```

3. Do not attempt to filter or fix the slug — Rippling's slug namespace is
   global and the collision cannot be resolved from the API side. Look for the
   company's actual career board through other channels (probe, website
   inspection).

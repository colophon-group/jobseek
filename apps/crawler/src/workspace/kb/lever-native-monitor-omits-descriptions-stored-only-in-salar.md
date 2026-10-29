---
step: select_monitor
symptom: Lever native monitor omits descriptions stored only in salaryDescription; plain description list with an each object raises field spec error.
tags: ['lever', 'api_sniffer', 'description', 'concat', 'each', 'salaryDescription']
---
# Lever native monitor omits descriptions stored only in salaryDescription; plain description list with an each object raises field spec error.

## Problem
Lever native monitor omits descriptions stored only in salaryDescription; plain description list with an each object raises field spec error.

## Solution
Inspect raw API fields and live detail pages. For CSC Generation, direct api_sniffer replay of Lever mode=json with limit=100 and skip pagination recovered all 471 jobs. Use description as a concat object with separator newline containing description, an each lists[*] wrapper emitting h3/text and ul/content, additional, and salaryDescription. This preserves section structure and recovers the two missing bodies. Map createdAt with timestamp_unit milliseconds. Generic mapper did not populate base_salary through direct, dotted, concat or JMES hash attempts; preserve source min/max/currency/interval as separate metadata leaves and explicitly rate normalized base_salary absent. Keep ambiguous bi-week-salary labels unchanged.

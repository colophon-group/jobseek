---
step: select_monitor
symptom: iCIMS board rendered page shows inconsistent results across runs
tags: ['icims', 'dom', 'render', 'static']
---
# iCIMS board rendered page shows inconsistent results across runs

## Problem
iCIMS board rendered page shows inconsistent results across runs

## Solution
Use static HTTP fetch (render: false) instead of Playwright rendering for iCIMS pages. iCIMS serves job listings via server-side HTML that is reliably available without JavaScript rendering.

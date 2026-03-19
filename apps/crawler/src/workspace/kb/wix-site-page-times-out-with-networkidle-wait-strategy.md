---
step: select_scraper
symptom: Wix site page times out with networkidle wait strategy
tags: ['wix', 'render', 'timeout', 'networkidle']
---
# Wix site page times out with networkidle wait strategy

## Problem
Wix site page times out with networkidle wait strategy

## Solution
Switch to wait: load strategy instead of networkidle. Wix sites make persistent background network requests that prevent networkidle from resolving.

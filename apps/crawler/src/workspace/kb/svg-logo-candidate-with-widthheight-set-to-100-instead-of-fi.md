---
step: setup
symptom: SVG logo candidate with width/height set to 100% instead of fixed pixel dimensions causes PNG conversion to fail with 'SVG size is undefined'
tags: ['logo', 'svg', 'conversion']
---
# SVG logo candidate with width/height set to 100% instead of fixed pixel dimensions causes PNG conversion to fail with 'SVG size is undefined'

## Problem
SVG logo candidate with width/height set to 100% instead of fixed pixel dimensions causes PNG conversion to fail with 'SVG size is undefined'

## Solution
Fall back to a different candidate that has fixed dimensions or an existing PNG preview. Candidates sourced from nav_svg often use percentage-based dimensions.

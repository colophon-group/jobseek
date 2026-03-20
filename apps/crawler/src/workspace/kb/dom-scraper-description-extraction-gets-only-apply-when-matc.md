---
step: select_scraper
symptom: DOM scraper description extraction gets only 'Apply' when matching container div with stop='Apply'
tags: ['dom-scraper', 'flat-dom', 'stop-condition']
---
# DOM scraper description extraction gets only 'Apply' when matching container div with stop='Apply'

## Problem
DOM scraper description extraction gets only 'Apply' when matching container div with stop='Apply'

## Solution
In flat DOM, wrapper divs (e.g. ibm-container-body) may appear AFTER their child elements, with only residual text like 'Apply' from nested buttons. Instead of matching the container div, match the last element before the content starts (e.g. a ref/subtitle paragraph) with offset=1, then collect from there with stop text.

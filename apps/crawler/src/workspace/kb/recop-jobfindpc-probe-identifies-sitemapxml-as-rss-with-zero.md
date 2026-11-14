---
step: configure_boards
symptom: Recop jobfind-pc probe identifies sitemap.xml as RSS with zero jobs; generic DOM includes category/application links and can truncate after a late HTTP405.
tags: ['recop', 'sitemap', 'rss', 'false-positive', 'pagination', 'json-ld']
---
# Recop jobfind-pc probe identifies sitemap.xml as RSS with zero jobs; generic DOM includes category/application links and can truncate after a late HTTP405.

## Problem
Recop jobfind-pc probe identifies sitemap.xml as RSS with zero jobs; generic DOM includes category/application links and can truncate after a late HTTP405.

## Solution
Validate the published sitemap as XML inventory, use sitemap monitor with an anchored /jobfind-pc/job/All/[0-9]+ detail-URL filter, and reconcile with the unfiltered All listing total. For Daisyo this retained1618 of1748 sitemap URLs in1.4s, exactly matching the live total; DOM stopped at1440 after page73 returned405. JSON-LD details expose title, description, municipality/prefecture, employment, dates and JPY salary. Distinguish erroneous upstream nearby-landmark prose from the actual structured address before judging location quality.

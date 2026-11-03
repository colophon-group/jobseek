---
step: configure
symptom: Hendrick Workday and JSON-LD have 100% location coverage but values are dealership or business names instead of cities.
tags: ['workday', 'locations', 'api-sniffer', 'hendrick', 'quality']
---
# Hendrick Workday and JSON-LD have 100% location coverage but values are dealership or business names instead of cities.

## Problem
Hendrick Workday and JSON-LD have 100% location coverage but values are dealership or business names instead of cities.

## Solution
Inspect semantic location values before accepting coverage. For Hendrick the public detail API jobPostingInfo.country.descriptor supplies truthful country-level locations; an HTTP api_sniffer scraper preserves full jobDescription HTML, maps timeType to normalized employment types and startDate to posting date. Full street addresses remain in descriptions. Record country-only granularity explicitly; do not claim city extraction or use a constant location default.

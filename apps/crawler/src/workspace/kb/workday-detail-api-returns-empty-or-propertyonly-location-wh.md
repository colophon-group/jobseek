---
step: configure_scraper
symptom: Workday detail API returns empty or property-only location while JSON-LD loses description HTML
tags: ['workday', 'locations', 'api_sniffer', 'html', 'talent-pools']
---
# Workday detail API returns empty or property-only location while JSON-LD loses description HTML

## Problem
Workday detail API returns empty or property-only location while JSON-LD loses description HTML

## Solution
Inspect the public CXS detail response. For MGM tenants jobPostingInfo.jobRequisitionLocation.country.descriptor provides geographic country even when location and country are absent. HTTP api_sniffer can retain jobDescription HTML and map country.descriptor || jobRequisitionLocation.country.descriptor, normalized timeType and startDate. Preserve property descriptors in metadata and explicitly disclose country-only precision. Verify every public site separately: generic talent pools and recruiting events can lack real vacancy descriptions and should be excluded rather than forced through quality gates.

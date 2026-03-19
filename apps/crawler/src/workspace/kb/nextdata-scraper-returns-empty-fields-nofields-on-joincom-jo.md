---
step: select_monitor
symptom: Nextdata scraper returns empty fields (no_fields) on join.com job pages with default config
tags: ['join.com', 'nextdata', 'empty-fields', 'field-mapping']
---
# Nextdata scraper returns empty fields (no_fields) on join.com job pages with default config

## Problem
Nextdata scraper returns empty fields (no_fields) on join.com job pages with default config

## Solution
Job data is at props.pageProps.initialState.job (not the default path). Configure nextdata with path and field mappings: title→title, description→schemaDescription, locations→city.cityName, employment_type→employmentType.googleType, job_location_type→workplaceType, date_posted→createdAt

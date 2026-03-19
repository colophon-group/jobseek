---
step: select_monitor
symptom: nextdata scraper returns empty fields on JOIN job pages despite __NEXT_DATA__ being present
tags: ['join', 'nextdata', 'scraper', 'empty-fields']
---
# nextdata scraper returns empty fields on JOIN job pages despite __NEXT_DATA__ being present

## Problem
nextdata scraper returns empty fields on JOIN job pages despite __NEXT_DATA__ being present

## Solution
Job data is at props.pageProps.initialState.job (not default path). Configure nextdata with path and field mappings: title=title, description=description, locations=city.cityName, employment_type=employmentType.googleType, job_location_type=workplaceType, date_posted=createdAt

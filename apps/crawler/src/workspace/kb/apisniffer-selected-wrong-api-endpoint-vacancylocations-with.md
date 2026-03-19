---
step: select_monitor
symptom: api_sniffer selected wrong API endpoint (vacancy-locations with 7 items instead of vacancies with 1 item) because it scored higher due to more items
tags: ['api_sniffer', 'peopleweek', 'intranet-digital', 'wrong-endpoint']
---
# api_sniffer selected wrong API endpoint (vacancy-locations with 7 items instead of vacancies with 1 item) because it scored higher due to more items

## Problem
api_sniffer selected wrong API endpoint (vacancy-locations with 7 items instead of vacancies with 1 item) because it scored higher due to more items

## Solution
When api_sniffer returns 0 jobs but logs show candidates, manually fetch the API base path with common resource names (vacancies, jobs, positions) to find the correct endpoint. PeopleWeek ATS (*.intranet.digital) uses /api/v1/external/recruitment/vacancies with include=locations for location data

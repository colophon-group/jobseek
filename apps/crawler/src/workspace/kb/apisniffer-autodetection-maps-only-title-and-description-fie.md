---
step: select_monitor
symptom: api_sniffer auto-detection maps only title and description fields, missing location data available in nested API response objects
tags: ['api_sniffer', 'field_mapping', 'locations', 'nested_fields']
---
# api_sniffer auto-detection maps only title and description fields, missing location data available in nested API response objects

## Problem
api_sniffer auto-detection maps only title and description fields, missing location data available in nested API response objects

## Solution
Inspect raw API response with curl to find nested location fields (e.g. city_info.en_name) and manually add them to the fields mapping in the monitor config. Same applies to other nested fields like recruit_type.en_name for employment_type.

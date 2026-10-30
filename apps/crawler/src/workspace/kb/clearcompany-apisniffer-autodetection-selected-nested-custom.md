---
step: configure
symptom: ClearCompany api_sniffer autodetection selected nested customFieldAnswers as five jobs although totalCount was one.
tags: ['clearcompany', 'api_sniffer', 'probe', 'pagination', 'field-mapping']
---
# ClearCompany api_sniffer autodetection selected nested customFieldAnswers as five jobs although totalCount was one.

## Problem
ClearCompany api_sniffer autodetection selected nested customFieldAnswers as five jobs although totalCount was one.

## Solution
Use the official apply.clearcompany.com board to establish its public careers-api.clearcompany.com/v1 tenant UUID. Map json_path results rather than results[0].customFieldAnswers, url_field applyLink, total_path totalCount, params pageSize 10 and pageIndex page pagination starting at zero. Map positionTitle, full HTML description, location, postedDate and locations[0].isRemote; preserve salaryRangeLow, salaryRangeHigh and salaryType in metadata when structured salary mapping is unavailable. The application page had no usable JSON-LD, so direct rich HTTP replay was the working route.

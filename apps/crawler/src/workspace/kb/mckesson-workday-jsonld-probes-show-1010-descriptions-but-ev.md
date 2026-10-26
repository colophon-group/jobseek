---
step: scraper
symptom: McKesson Workday JSON-LD probes show 10/10 descriptions but every extracted description is only identical company boilerplate; TELECOMMUTE appears on onsite delivery and warehouse jobs.
tags: ['workday', 'json-ld', 'mckesson', 'boilerplate', 'job-location-type']
---
# McKesson Workday JSON-LD probes show 10/10 descriptions but every extracted description is only identical company boilerplate; TELECOMMUTE appears on onsite delivery and warehouse jobs.

## Problem
McKesson Workday JSON-LD probes show 10/10 descriptions but every extracted description is only identical company boilerplate; TELECOMMUTE appears on onsite delivery and warehouse jobs.

## Solution
Use the native workday detail API scraper. It returned complete role HTML plus explicit onsite/hybrid/remote on all 10 tested samples. Inspect semantic content rather than trusting nonempty field counts; reject the JSON-LD alternative despite a successful probe.

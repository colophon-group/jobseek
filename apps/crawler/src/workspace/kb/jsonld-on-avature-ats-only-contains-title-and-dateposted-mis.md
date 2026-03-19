---
step: select_scraper
symptom: JSON-LD on Avature ATS only contains title and datePosted, missing description and location
tags: ['avature', 'json-ld', 'dom', 'scraper']
---
# JSON-LD on Avature ATS only contains title and datePosted, missing description and location

## Problem
JSON-LD on Avature ATS only contains title and datePosted, missing description and location

## Solution
Switch to DOM scraper. Avature job pages have structured DOM: h2 for title, labeled fields (City, Date published) in Basic Information section, and Job description section. Use stop=Apply to avoid capturing the Apply button in description.

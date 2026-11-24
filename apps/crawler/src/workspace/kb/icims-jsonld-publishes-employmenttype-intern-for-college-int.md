---
step: configure
symptom: iCIMS JSON-LD publishes employmentType INTERN for college internships, which is seniority rather than a contract type.
tags: ['icims', 'embedded', 'json-ld', 'internship', 'seniority', 'employment-type']
---
# iCIMS JSON-LD publishes employmentType INTERN for college internships, which is seniority rather than a contract type.

## Problem
iCIMS JSON-LD publishes employmentType INTERN for college internships, which is seniority rather than a contract type.

## Solution
When the JobPosting schema has full title/description/address/dates but json-ld has no field mapping, use the existing embedded scraper on the application/ld+json script. Preserve title, description, city/state/country and dates; map employmentType INTERN to metadata.seniority intern and omit the invalid employment_type. Verify the Internship title matches canonical Intern taxonomy. Do not infer salary from compensation prose when baseSalary is absent.

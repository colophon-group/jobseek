---
step: configure
symptom: IdealTraits careers pages expose /careers?page=2 navigation even when all current postings fit on one page; generic DOM link detection counts pagination as a job.
tags: ['idealtraits', 'dom', 'json-ld', 'pagination', 'count-validation']
---
# IdealTraits careers pages expose /careers?page=2 navigation even when all current postings fit on one page; generic DOM link detection counts pagination as a job.

## Problem
IdealTraits careers pages expose /careers?page=2 navigation even when all current postings fit on one page; generic DOM link detection counts pagination as a job.

## Solution
Use static DOM link_selector a[href*="/career/"] to select actual singular /career/<tenant>/<id> detail links. Verify completeness with advertised_total selector a.location-option[data-location="all"] and regex ^All\s*\((\d+)\)$. Match actual link count to the advertised All(N) total; exclude /careers navigation. IdealTraits details provide full structured JobPosting JSON-LD; responsibilities may remain an HTML block and must be rated noisy rather than clean plain-text bullets.

---
step: configure
symptom: Academic Solr API auto-detection selected shared unit logo URLs and collapsed a 390-job board to ten URLs.
tags: ['api_sniffer', 'solr', 'url-template', 'university-of-chicago']
---
# Academic Solr API auto-detection selected shared unit logo URLs and collapsed a 390-job board to ten URLs.

## Problem
Academic Solr API auto-detection selected shared unit logo URLs and collapsed a 390-job board to ten URLs.

## Solution
On academicjobs.uchicago.edu use the observed canonical https://academicjobs.uchicago.edu/positions/{id} URL template, response.docs and response.numFound. Direct HTTP rows=500 with offset pagination captured all 390 positions in 1.3 seconds. Assemble description, qualifications, instructions, salary_range and EEO; keep textual dates and academic categories in metadata. Browser detail replay adds no content and costs about five seconds per job.

---
step: select_scraper
symptom: ChenMed Workday API locations were facility labels; its JSON-LD repeated those labels and falsely marked onsite clinical roles TELECOMMUTE. The branded Phenom sitemap had fewer jobs than the live search inventory.
tags: ['workday', 'phenom', 'nextdata', 'embedded', 'locations', 'pagination']
---
# ChenMed Workday API locations were facility labels; its JSON-LD repeated those labels and falsely marked onsite clinical roles TELECOMMUTE. The branded Phenom sitemap had fewer jobs than the live search inventory.

## Problem
ChenMed Workday API locations were facility labels; its JSON-LD repeated those labels and falsely marked onsite clinical roles TELECOMMUTE. The branded Phenom sitemap had fewer jobs than the live search inventory.

## Solution
Verify source identity by matching requisition IDs, then compare source quality. ChenMed Phenom canvas data provided geographic multi-location objects and full HTML descriptions. Its nextdata search data at eagerLoadRefineSearch.data.jobs paginated completely using totalHits, page_size 10, start 0 and the same-origin query template from={page}0&s=1; 49 pages yielded all 486 advertised jobs. Embedded phApp.ddo jobDetail.data.job preserved HTML, multi_location[].location and ml_skills. Map work mode only from explicit published LI tags, leaving absent tags null. Reverify these tenant-specific observations before reuse.

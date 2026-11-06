---
step: configure
symptom: Workday all-sites discovery includes a separately branded employer in the same tenant (Uline and EAU Holdings)
tags: ['workday', 'multi-site', 'employer-identity', 'uline']
---
# Workday all-sites discovery includes a separately branded employer in the same tenant (Uline and EAU Holdings)

## Problem
Workday all-sites discovery includes a separately branded employer in the same tenant (Uline and EAU Holdings)

## Solution
Verify every discovered tenant site employer before accepting all_sites. Uline tenant robots lists Uline_Careers and EAU_Careers; native runs returned 412 Uline postings and 3 EAU hospitality postings (415 aggregated). Official EAU Dishwasher R267075 identifies About EAU Holdings, while Uline Mexico R267313 and Canada R268744 identify the shipping supplies employer. Configure Uline with all_sites:false to exclude the separate EAU site. A successful detail request alone does not prove list membership because this tenant accepts a requisition across either site path.

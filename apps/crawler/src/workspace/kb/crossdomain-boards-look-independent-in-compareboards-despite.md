---
step: configure_boards
symptom: Cross-domain boards look independent in compare-boards despite mirroring the same requisitions
tags: ['overlap', 'workday', 'sitemap', 'rentokil-initial']
---
# Cross-domain boards look independent in compare-boards despite mirroring the same requisitions

## Problem
Cross-domain boards look independent in compare-boards despite mirroring the same requisitions

## Solution
compare-boards compares stored URL samples, so different public domains can report zero overlap. Verify semantic identity by following official Apply links and matching exact ATS requisition IDs before retaining both sources. For Rentokil Initial, global jid51979 applies to Terminix Workday R-071738 and global jid53462 to R-069134; the global portal also covers other countries and provides readable cities where native Workday returned internal site codes. Check sample field quality and broader coverage before dropping an overlapping source; sampled matches alone do not prove complete containment.

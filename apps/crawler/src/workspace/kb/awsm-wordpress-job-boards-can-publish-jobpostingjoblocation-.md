---
step: verify_and_feedback
symptom: AWSM WordPress job boards can publish JobPosting.jobLocation as work-mode labels such as Remoto/Híbrido; sitemap may additionally include a generic talent-pool page.
tags: ['awsm', 'wordpress', 'json-ld', 'locations', 'sitemap', 'megawork']
---
# AWSM WordPress job boards can publish JobPosting.jobLocation as work-mode labels such as Remoto/Híbrido; sitemap may additionally include a generic talent-pool page.

## Problem
AWSM WordPress job boards can publish JobPosting.jobLocation as work-mode labels such as Remoto/Híbrido; sitemap may additionally include a generic talent-pool page.

## Solution
Check semantic values, not field counts. For GFT Megawork, expand the authoritative listing with repeat on a.awsm-load-more:26 roles after two clicks. Sitemap27 exactly matched those26 plus banco-de-talentos; exclude only the verified pool. Static DOM details scoped to main#content extract geographic Estado from awsm-job-specification-job-estado, employment from job-type, mode from job-location, and HTML description from first p until Me candidatar para a vaga. Do not treat mode labels as geography or invent locations for upstream remote-only roles. Keep source-local optional enum values rated noisy when unnormalized. Verify full sample fields and record any local parser limitation explicitly.

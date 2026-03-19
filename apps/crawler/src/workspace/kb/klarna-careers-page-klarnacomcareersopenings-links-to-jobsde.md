---
step: add_boards
symptom: Klarna careers page (klarna.com/careers/openings) links to jobs.deel.com/job-boards/klarna but ws probe monitor on the Deel URL does not detect the deel monitor. Instead, ashby is detected with token 'deel' which returns Deel's own 239 jobs, not Klarna's.
tags: ['deel', 'probe-false-negative', 'ashby-false-positive', 'manual-config']
---
# Klarna careers page (klarna.com/careers/openings) links to jobs.deel.com/job-boards/klarna but ws probe monitor on the Deel URL does not detect the deel monitor. Instead, ashby is detected with token 'deel' which returns Deel's own 239 jobs, not Klarna's.

## Problem
Klarna careers page (klarna.com/careers/openings) links to jobs.deel.com/job-boards/klarna but ws probe monitor on the Deel URL does not detect the deel monitor. Instead, ashby is detected with token 'deel' which returns Deel's own 239 jobs, not Klarna's.

## Solution
The deel monitor probe checks for jobs.deel.com/{slug} URL pattern but the board URL was jobs.deel.com/job-boards/klarna. Manually configure deel monitor with slug 'klarna' via ws select monitor deel --config '{"slug": "klarna"}'. The slug is the company name in the job-boards path, not the domain.

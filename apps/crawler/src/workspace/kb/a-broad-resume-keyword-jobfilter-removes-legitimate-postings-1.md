---
step: monitor
symptom: A broad resume keyword job_filter removes legitimate postings whose anti-scam descriptions mention resumes.
tags: ['job-filter', 'false-positives', 'mokahr', 'quality']
---
# A broad resume keyword job_filter removes legitimate postings whose anti-scam descriptions mention resumes.

## Problem
A broad resume keyword job_filter removes legitimate postings whose anti-scam descriptions mention resumes.

## Solution
Rich monitor job_filter searches titles, descriptions, locations and metadata. Inspect source rows before excluding talent-pool placeholders; use exact stable URL/provider IDs for confirmed collection-only or empty-description records, then re-run counts and required-field checks. For BeOne, broad Chinese resume text matched 456 rows including many genuine roles, while eight exact URL exclusions retained 477 complete postings from 485 active records.

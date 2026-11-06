---
step: configure
symptom: Career PDF links appear in an inline newsInfo.articleContent HTML string but static DOM discovery returns zero actual jobs
tags: ['dom', 'render', 'pdf', 'embedded-html', 'regional-mirrors']
---
# Career PDF links appear in an inline newsInfo.articleContent HTML string but static DOM discovery returns zero actual jobs

## Problem
Career PDF links appear in an inline newsInfo.articleContent HTML string but static DOM discovery returns zero actual jobs

## Solution
Verify the official listing contains genuine PDF vacancy links, use rendered DOM with a selector scoped to the career document directory and resource_policy none, then pair with pdf scraper. Derive title from the documented filename prefix and location from the PDF Location label; verify every PDF and retain country-only locations when that is what the document publishes. On Ninebot Europe this recovered two postings; Dutch and Spanish pages were subsets of the Europe list.

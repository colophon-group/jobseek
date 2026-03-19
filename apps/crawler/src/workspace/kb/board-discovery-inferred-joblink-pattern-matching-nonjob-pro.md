---
step: add_boards
symptom: Board discovery inferred job-link pattern matching non-job product pages (e.g. /bank/currency-converter, /bank/savings-account) because Gatsby SPA careers page had few static links and the auto-detection latched onto a common URL prefix
tags: ['board-discovery', 'spa', 'ats', 'job-link-pattern']
---
# Board discovery inferred job-link pattern matching non-job product pages (e.g. /bank/currency-converter, /bank/savings-account) because Gatsby SPA careers page had few static links and the auto-detection latched onto a common URL prefix

## Problem
Board discovery inferred job-link pattern matching non-job product pages (e.g. /bank/currency-converter, /bank/savings-account) because Gatsby SPA careers page had few static links and the auto-detection latched onto a common URL prefix

## Solution
When the careers page is a JS SPA embedding jobs from an external ATS, ignore auto-inferred patterns on the main domain. Instead, inspect static HTML for outgoing ATS links (e.g. alpian.intranet.digital) and use the ATS as the board URL

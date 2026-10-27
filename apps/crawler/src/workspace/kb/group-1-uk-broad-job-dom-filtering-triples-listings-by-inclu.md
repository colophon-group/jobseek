---
step: configure
symptom: Group 1 UK broad /job/ DOM filtering triples listings by including /apply and /save_job; the sitemap omits current listing jobs.
tags: ['group-1-automotive', 'dom', 'pagination', 'json-ld', 'duplicate-links', 'sitemap']
---
# Group 1 UK broad /job/ DOM filtering triples listings by including /apply and /save_job; the sitemap omits current listing jobs.

## Problem
Group 1 UK broad /job/ DOM filtering triples listings by including /apply and /save_job; the sitemap omits current listing jobs.

## Solution
Use the unfiltered official UK /jobs/ board with rendered DOM pagination (page parameter, browser true, transient_403 true) and canonical single-segment URL filter /job/[^/?#]+/?$. Live verification yielded208 canonical jobs over26pages versus197 in the sitemap; page27 had no new URLs. Pair with rendered JSON-LD; static HTTP returned202. Keep native unblocked resources (monitor policy none; JSON-LD scraper render:true uses default none). Required fields were clean; zero salary values lacking currency/unit were upstream placeholders and rated unusable.

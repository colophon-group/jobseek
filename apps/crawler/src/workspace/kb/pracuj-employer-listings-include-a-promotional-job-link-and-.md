---
step: scraper
symptom: Pracuj employer listings include a promotional job link and JSON-LD descriptions omit requirements and benefits; sniffed jobCenterOffers listing ignores eid and returns unrelated jobs.
tags: ['pracuj', 'poland', 'nextdata', 'employer-scope', 'description-completeness']
---
# Pracuj employer listings include a promotional job link and JSON-LD descriptions omit requirements and benefits; sniffed jobCenterOffers listing ignores eid and returns unrelated jobs.

## Problem
Pracuj employer listings include a promotional job link and JSON-LD descriptions omit requirements and benefits; sniffed jobCenterOffers listing ignores eid and returns unrelated jobs.

## Solution
On the verified employer listing select only a[data-test=link-offer-title], reconcile the displayed total, and test pagination. Prefer nextdata detail fields under props.pageProps.dehydratedState.queries[0].state.data after verifying the jobOffer query and employer ID. Concatenate textSections into HTML to retain all sections, map workSchedules and workModes to canonical enums, and preserve dates, responsibilities and qualifications. Arthrex verified all three employer jobs; the generic API response and responsibilities-only JSON-LD were rejected.

---
step: pre_verify
symptom: Issue requests a subsidiary company whose jobs are listed on a centralized parent company careers portal (e.g. SWISS jobs on Lufthansa Group portal, Fiat jobs on Stellantis portal).
tags: ['subsidiary', 'parent-org', 'centralized-portal', 'group', 'division']
---
# Subsidiary jobs listed on parent company centralized careers portal

## Problem
The requested company is a subsidiary or brand within a larger group. It does
not operate its own careers page — all job listings are hosted on the parent
company's centralized careers portal, often filterable by division or brand.

Examples:
- **Swiss International Air Lines** → jobs at `apply.lufthansagroup.careers` (Lufthansa Group portal, filtered by SWISS division)
- **Fiat** → jobs at Stellantis careers portal
- **Instagram** → jobs at Meta careers portal

## How to detect
1. The subsidiary's own website has no `/careers` or `/jobs` page, or it redirects to a parent domain.
2. Web search for `"<subsidiary> careers"` leads to the parent company's portal.
3. The careers portal URL contains the parent company's name (e.g. `lufthansagroup.careers`), not the subsidiary's.
4. The portal shows jobs for multiple brands/divisions with a division filter.

## Solution
**Configure the parent company, not the subsidiary.** The parent's portal is
the actual data source and covers all subsidiaries in one place.

1. Reject the subsidiary issue explaining the situation:
   ```bash
   ws reject --issue <N> --reason subsidiary --message "<Subsidiary> does not have its own careers page. All jobs are listed on the <Parent> Group careers portal at <URL>. Configuring <Parent> Group instead, which covers all subsidiaries."
   ```
2. Create a new workspace for the parent company:
   ```bash
   ws new <parent-slug> --issue <N>
   ```
3. Configure the parent's centralized portal as the board. If the portal
   supports division filters, consider:
   - **One board for everything** if all divisions share the same listing page and API
   - **Separate boards per division** only if divisions have distinct URLs or ATS platforms (like Glencore)

## When to configure the subsidiary instead
If the subsidiary has its own independent careers page with its own job
listings (not just a redirect to the parent), configure the subsidiary
directly. This is common when:
- The subsidiary was recently acquired and still runs its own ATS
- The subsidiary operates in a different industry from the parent
- The parent company is a holding/investment company with no unified portal

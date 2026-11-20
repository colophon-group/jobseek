---
step: select_monitor
symptom: Saba Talentlink API discovery continued past the advertised inventory and returned HTTP 400; auto-detected URLs pointed to application forms.
tags: ['saba', 'talentlink', 'api_sniffer', 'pagination', 'total_path']
---
# Saba Talentlink API discovery continued past the advertised inventory and returned HTTP 400; auto-detected URLs pointed to application forms.

## Problem
Saba Talentlink API discovery continued past the advertised inventory and returned HTTP 400; auto-detected URLs pointed to application forms.

## Solution
For the verified Novolex Saba public jobs API, configure total_path globals.jobsCount and offset pagination firstResult with matching maxResults/increment. The API returns full role sections under customFields[].content, title jobFields.jobTitle, and location parts SLOCATION/REGLABEL/COUNTRYLABEL. Verify and use the official details.html?jobId={id} URL template instead of applicationUrl. Do not assume a portal linked as Europe is Europe-only: this inventory also included Canada.

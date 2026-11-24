---
type: case-study
company: konecranes
monitor: smartrecruiters
scraper: smartrecruiters
summary: "Konecranes uses one global SmartRecruiters tenant with 414 live jobs versus official portal396 and inventory406. Corporate and careers.smartrecruiters URLs redirect to konecranes.careers; regional searches are country filters and Demag, R&M and SWF share central listings. Verified US744000163322319, China744000097190786, India744000162657569 and Brazil744000162155667 apply IDs. South Africa related widgets show expired roles while current filtered search is empty: do not add a stale regional mirror. Native detail scraper produced title, long HTML description and location10/10, employment and dates10/10, salary4/10. Work mode is noisy: field-service Shreveport and Appleton flagged remote upstream while official roles require customer-site work; Mechanical Structural Engineer lacks API work mode despite portal Hybrid. Record acceptable verdict and explicit limitation. Corporate homepage403 yielded only favicon; discovery on official career site supplied header SVG and distinct C-mark icon, then company website restored."
tags: ['smartrecruiters', 'centralized', 'multiregion', 'brand-portals', 'work-mode']
---
# Konecranes — Konecranes uses one global SmartRecruiters tenant with 414 live jobs versus official portal396 and inventory406. Corporate and careers.smartrecruiters URLs redirect to konecranes.careers; regional searches are country filters and Demag, R&M and SWF share central listings. Verified US744000163322319, China744000097190786, India744000162657569 and Brazil744000162155667 apply IDs. South Africa related widgets show expired roles while current filtered search is empty: do not add a stale regional mirror. Native detail scraper produced title, long HTML description and location10/10, employment and dates10/10, salary4/10. Work mode is noisy: field-service Shreveport and Appleton flagged remote upstream while official roles require customer-site work; Mechanical Structural Engineer lacks API work mode despite portal Hybrid. Record acceptable verdict and explicit limitation. Corporate homepage403 yielded only favicon; discovery on official career site supplied header SVG and distinct C-mark icon, then company website restored.

## Setup
- Monitor: smartrecruiters
- Scraper: smartrecruiters

## Key decisions
- Keep one unfiltered central board. The official global search at https://konecranes.careers/jobs and regional pages share the Konecranes SmartRecruiters tenant. Official apply links from the US, China, India and Brazil confirmed the tenant; Demag, R&M and SWF also use the central inventory.
- On 2026-11-24 the monitor found 414 jobs in 1.7 seconds, compared with the issue inventory estimate of 406 and official portal count of 396. Ten detail samples across six countries had complete titles, descriptions and geographic locations. Employment type and dates were present in all samples, salary in four and work mode in three.
- Rate work mode as noisy: the API marks field-service Shreveport and Appleton roles remote although the official pages describe customer-site work. The Mechanical Structural Engineer page says Hybrid, which its API extraction omits. The provider scraper has no documented field overrides; retain an acceptable verdict with this limitation recorded.
- Do not add South Africa from its related-jobs widget: sampled roles had expired and the current country-filtered search was empty.
- The corporate homepage returned 403 and discovery found only a small favicon. Discovery on the official career site supplied a header SVG and distinct C-mark icon; restore the corporate website after selecting assets.

## Config
Board URL: `https://careers.smartrecruiters.com/Konecranes`

Monitor config:

```json
{"token": "Konecranes"}
```

Scraper: auto-configured `smartrecruiters`, with no additional options. Both monitor and scraper use direct HTTP APIs without browser rendering.

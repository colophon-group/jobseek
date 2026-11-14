---
type: case-study
company: home-genius-exteriors
monitor: sitemap
scraper: json-ld
summary: "Home Genius Exteriors has distinct Breezy and official market boards; website JSON-LD needs full-description and region overrides, with salary/date quality caveats."
tags: ['breezy', 'sitemap', 'json-ld', 'description-selector', 'market-locations', 'salary-quality']
---
# Home Genius Exteriors — Breezy and official market boards

## Setup
- Monitor: sitemap
- Scraper: json-ld

## Key decisions
- Keep both sources: Breezy has 409 public positions; the official website has 41 market-specific listings with independent application forms and position IDs. URL comparison found no shared identities; neither source was demonstrated to be a mirror or subset.
- Prefer the sitemap (0.3 seconds) over DOM cards (0.4 seconds). Both find 41 roles, and decorated card URLs provide no extraction advantage over canonical sitemap URLs.
- Replace the short JSON-LD description with `.post-content`. Ten initial samples contain complete responsibilities, qualifications and benefits; three final samples validate the location cleanup.
- Omit the provider's incorrect addressRegion, which attaches PA to the multistate Mid-Atlantic market. Broad market names remain legitimate locations.
- Rate optional salary and posting-date fields honestly: schema salary minimums can be 90 or 35 instead of 90000 or 35000, and the posting date is always 2025-01-01. Do not infer field quality from non-empty coverage.
- Static HTTP succeeds for both sources despite transient 429s during probing. Rendering, proxies and resource blocking are unnecessary for the final configurations.

## Config
Official website board: `https://homegeniusexteriors.com/careers/`

Monitor (`sitemap`):
```json
{"sitemap_url":"https://homegeniusexteriors.com/sitemap.xml","url_filter":"/careers/[^/]+/?$"}
```

Scraper (`json-ld`):
```json
{"description_selector":".post-content","ignore_address_region":true}
```

Breezy board: `https://home-genius-exteriors.breezy.hr/`, native `breezy` monitor and automatic `json-ld` scraper. Ten samples have clean required fields. Upstream employment/remote labels and salary semantics need caution; the setup parser also loses scalar weekly salary amounts while retaining range amounts.

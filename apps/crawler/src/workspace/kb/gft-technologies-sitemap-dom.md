---
type: case-study
company: gft-technologies
monitor: sitemap
scraper: dom
summary: "Global GFT SuccessFactors feed plus acquired Megawork's separate WordPress board; verify sitemap completeness and extract geographic Estado instead of JSON-LD work modes."
tags: ['successfactors', 'awsm', 'wordpress', 'multiboard', 'locations']
---
# GFT Technologies and Megawork

## Setup
- Monitor: sitemap
- Scraper: dom

## Key decisions
- All 25 official GFT career locale variants led to the central jobs.gft.com SuccessFactors tenant. Its rich RSS feed returned 383 jobs with full titles, descriptions and locations; JSON-LD detail extraction returned no usable JobPosting data on tested GFT URLs.
- GFT's acquisition announcement and Megawork's official branding established a separate subsidiary source at talentos.megawork.com.br. Its initial listing contained 12 links. Two clicks on `a.awsm-load-more` expanded the complete listing to 26 roles and removed the control. The sitemap had exactly those 26 URLs plus a generic `banco-de-talentos` pool; exclude that verified non-role page.
- Megawork JobPosting JSON-LD had substantive role descriptions and dates, but `jobLocation` held work-mode strings such as Remoto and Híbrido. DOM `Estado` labels supplied real geography. Keep remote-only postings without invented geography; preserve and rate localized employment/work-mode fields as noisy. The WordPress REST endpoint exposed complete descriptions and dates but no geographic taxonomy display names, so it did not replace DOM extraction.
- The final static sitemap took about one second versus 11.9 seconds for rendered listing expansion. Both none and lean listing controls found 26 roles, but final configuration requires no browser.
- The resolver host lacked `/usr/local/bin/dom-detail-parse`. Repository documentation in `go/dom-detail/README.md` confirmed the existing reference parser selector for offline verification. Ten random samples and three final targeted samples verified titles, full descriptions, geography where supplied, and explicit remote modes. No parser override was persisted in board configuration; this was offline reference verification, not an installed native-runtime qualification.

## Config

Megawork monitor:

```json
{"sitemap_url":"https://talentos.megawork.com.br/sitemap.xml","url_filter":{"include":"/vagas/","exclude":"/banco-de-talentos/"}}
```

Megawork scraper:

```json
{"render":false,"scope":"main#content","steps":[{"tag":"h1","field":"title"},{"tag":"div","attr":"class=awsm-job-specification-job-type","field":"employment_type","regex":"Regime de Contratação:[ ]*(.+)","optional":true},{"tag":"div","attr":"class=awsm-job-specification-job-location","field":"job_location_type","regex":"Modelo de Contratação:[ ]*(.+)","optional":true},{"tag":"div","attr":"class=awsm-job-specification-job-estado","field":"locations","regex":"Estado:[ ]*(.+)","optional":true},{"tag":"p","field":"description","html":true,"stop":"Me candidatar para a vaga","from":0}]}
```

Main GFT board uses the validated `rss` SuccessFactors inventory seed and `skip` details. Board comparison found zero shared URLs. Both sources received acceptable verdicts with optional-field limitations recorded.

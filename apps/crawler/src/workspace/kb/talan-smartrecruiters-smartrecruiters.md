---
type: case-study
company: talan
monitor: smartrecruiters
scraper: smartrecruiters
summary: "Talan worldwide uses main SmartRecruiters tenant talan (464 jobs) plus distinct Dataroots Recruitee custom domain (14) and Nine Feet Tall Breezy (3). Regional Talan and Nova portals are central-tenant subsets. Coexya recruitment sitemap has 37 /offer/ URLs with different hosts/identifiers, so URL-only comparison misses duplicates: four tested roles also occur on Talan SmartRecruiters, including Java developer 744000143261609 with matching Lyon location and posting timestamp, Architecte Industrie 4.0 744000139553839, Endpoint Management 744000148969344 and IA Agents 744000150098993. Exclude that rendered mirror in favor of native direct API. Coexya token returns zero despite 37 visible jobs; do not accept inferred ATS token without live verification."
tags: ['smartrecruiters', 'recruitee', 'breezy', 'subsidiaries', 'mirror-board', 'coexya']
---
# Talan — Talan worldwide uses main SmartRecruiters tenant talan (464 jobs) plus distinct Dataroots Recruitee custom domain (14) and Nine Feet Tall Breezy (3). Regional Talan and Nova portals are central-tenant subsets. Coexya recruitment sitemap has 37 /offer/ URLs with different hosts/identifiers, so URL-only comparison misses duplicates: four tested roles also occur on Talan SmartRecruiters, including Java developer 744000143261609 with matching Lyon location and posting timestamp, Architecte Industrie 4.0 744000139553839, Endpoint Management 744000148969344 and IA Agents 744000150098993. Exclude that rendered mirror in favor of native direct API. Coexya token returns zero despite 37 visible jobs; do not accept inferred ATS token without live verification.

## Setup
- Monitor: smartrecruiters
- Scraper: smartrecruiters

## Key decisions
- Verify the inventory seed live: the `talan` tenant returned 464 jobs versus 466 at issue creation. Ten sampled details had clean titles, complete descriptions and locations.
- Research the group's operating brands. The official group ecosystem linked Dataroots and Nine Feet Tall; neither had a registry match. Their separate live boards returned 14 and 3 jobs respectively.
- Exclude regional Talan and Nova views of the central tenant. Coexya's separate recruitment portal also mirrors parent jobs: four checked roles matched Talan SmartRecruiters, including the Java developer's title, Lyon location and posting timestamp. URL overlap alone missed this because the portal uses different URLs. The main monitor artifact also stores only its first 100 URLs, limiting automated comparisons.
- A guessed SmartRecruiters `coexya` token returned zero despite 37 visible jobs. The portal's filtered sitemap did return 37 jobs, and rendered JSON-LD extracted required fields for ten samples, but the duplicate source adds rendering cost. Keep the native parent feed instead.
- Nine Feet Tall's JSON-LD supplies all required fields but omits hybrid work mode and uses FULL_TIME even where prose allows flexible/part-time arrangements. Record an acceptable verdict and the optional-field limitations; retain complete descriptions.

## Config
| Board | Monitor | Monitor config | Scraper |
| --- | --- | --- | --- |
| `https://careers.smartrecruiters.com/talan` | `smartrecruiters` | `{"token":"talan"}` | Auto-configured `smartrecruiters` |
| `https://careers.dataroots.io/` | `recruitee` | `{"api_base":"https://careers.dataroots.io"}` | `skip` (rich API) |
| `https://nine-feet-tall.breezy.hr/` | `breezy` | `{"portal_url":"https://nine-feet-tall.breezy.hr"}` | Auto-configured `json-ld` |

All retained configurations use static HTTP/API processing. No browser resource policy is required.

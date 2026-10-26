---
type: case-study
company: bodelschwinghsche-stiftungen-bethel
monitor: sitemap
scraper: json-ld
summary: "Bethel central SuccessFactors feed matches 470 live jobs. Eben-Ezer adds 47 independent CMS jobs: sitemap excludes thank-you page /danke and exactly matches CMS API URLs. JSON-LD descriptions are introductions only, so a required browser action joins all role sections and excludes forms. Reconcile locations with the official per-job CMS address; one guarded text-backed Leopoldshoehe exception corrects a contact-address error. Omit resource_policy for json-ld registry schema compatibility; omitted policy disables blocking."
tags: ['bethel', 'cms', 'json-ld', 'sitemap', 'locations']
---
# Bethel: complete CMS descriptions and correct role locations

## Setup

The central SuccessFactors feed returned 470 jobs, exactly matching the official search total. The separate Eben-Ezer CMS returned 47 jobs. The final sitemap URL set exactly matched the CMS API result; board comparison found no shared URLs with the central feed.

## Key decisions

- Filter the sitemap to actual job-detail paths and exclude `/karriere/jobs-in-eben-ezer/danke`, a thank-you page that otherwise inflated the count to 48.
- Static JSON-LD descriptions contain introductions only. A selector for `.text-image__text` returns only the first element. The required browser action joins all role sections into a dedicated container, retaining duties and requirements while excluding application forms and contacts.
- JSON-LD cities sometimes reflect contact addresses. Reconcile each exact job URL with the official unfiltered CMS API `/?type=170011&recordResultList=24277` and its `address.city`. Fail on missing or ambiguous matches. The Leopoldshöhe posting has an additional guarded exception supported by its role introduction; remove stale street/postcode values when changing the city.
- Select sitemap over the API alternative after exact 47-URL parity: approximately 1.0 versus 3.2 seconds per monitor cycle. Both use the same verified scraper.
- Omit `resource_policy`: the default blocks nothing, while an explicit key currently fails the JSON-LD registry census schema. None/lean samples had field parity, but artifacts did not establish actual font/media blocking, so no blocking policy was accepted.

## Verification

Ten varied samples contained full duties and requirements without form text. Four targeted final samples returned Leopoldshöhe, Lage, Bad Salzuflen and Lemgo correctly, with clean titles, descriptions, employment types and posting dates. The final omitted-policy run passed 4/4 at approximately 4.7 seconds per page. Artifacts: `scraper/run-20261026T162320` (broader descriptions), `scraper/run-20261026T162856` (final selected configuration).

## Config

```json
{
  "board_url": "https://www.eben-ezer.de/karriere/jobs-in-eben-ezer",
  "monitor_type": "sitemap",
  "monitor_config": {
    "sitemap_url": "https://www.eben-ezer.de/sitemap.xml",
    "url_filter": {
      "include": "^https://www\\.eben-ezer\\.de/karriere/jobs-in-eben-ezer/.+",
      "exclude": "/karriere/jobs-in-eben-ezer/danke(?:[/?#]|$)"
    }
  },
  "scraper_type": "json-ld",
  "scraper_config": {
    "render": true,
    "description_selector": "#jobseek-description",
    "actions": [
      {
        "action": "evaluate",
        "script": "(async () => {\nconst parts = Array.from(document.querySelectorAll('#main-content .text-image__text'));\nconst box = document.createElement('div'); box.id = 'jobseek-description';\nfor (const part of parts) box.appendChild(part.cloneNode(true));\nif (!parts.length || box.textContent.trim().length < 200) throw new Error('Missing complete role sections');\ndocument.body.appendChild(box);\nconst normalize = value => { const url = new URL(value, location.origin); return url.origin + (url.pathname.endsWith('/') ? url.pathname.slice(0, -1) : url.pathname); };\nconst current = normalize(location.href);\nconst response = await fetch('/?type=170011&recordResultList=24277', {credentials: 'omit'});\nif (response.status !== 200) throw new Error('Official role inventory unavailable');\nconst payload = await response.json();\nif (!Array.isArray(payload.result)) throw new Error('Official role inventory changed');\nconst matches = payload.result.filter(item => typeof item.url === 'string' && normalize(item.url) === current);\nif (matches.length !== 1) throw new Error('Missing or ambiguous official role address');\nlet city = matches[0].address && matches[0].address.city;\nif (typeof city !== 'string' || !city.trim()) throw new Error('Missing official role city');\ncity = city.trim();\nconst exception = 'https://www.eben-ezer.de/karriere/jobs-in-eben-ezer/erzieher-/-paedagogische-fachkraft-m/w/d';\nif (current === exception) {\n if (!parts[0].textContent.includes('Leopoldshöhe')) throw new Error('Location evidence changed');\n city = 'Leopoldshöhe';\n}\nlet corrected = 0;\nconst visit = value => {\n if (!value || typeof value !== 'object') return;\n const types = Array.isArray(value['@type']) ? value['@type'] : [value['@type']];\n if (types.includes('JobPosting')) {\n  const places = Array.isArray(value.jobLocation) ? value.jobLocation : [value.jobLocation];\n  if (places.length !== 1 || !places[0] || !places[0].address) throw new Error('Structured location shape changed');\n  const address = places[0].address;\n  if (current === exception && address.addressLocality !== 'Lemgo') throw new Error('Exception structured location changed');\n  if (address.addressLocality !== city) { address.addressLocality = city; delete address.streetAddress; delete address.postalCode; }\n  corrected++;\n }\n for (const child of Object.values(value)) if (child && typeof child === 'object') { if (Array.isArray(child)) child.forEach(visit); else visit(child); }\n};\nfor (const node of document.querySelectorAll('script[type=\"application/ld+json\"]')) {\n let data; try { data = JSON.parse(node.textContent); } catch { continue; }\n visit(data); node.textContent = JSON.stringify(data);\n}\nif (!corrected) throw new Error('No JobPosting location to reconcile');\n})()",
        "required": true
      }
    ]
  }
}
```

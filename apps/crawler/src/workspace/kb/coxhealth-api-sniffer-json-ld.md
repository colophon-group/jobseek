---
type: case-study
company: coxhealth
monitor: api_sniffer
scraper: json-ld
summary: "CoxHealth has 510 Workday jobs plus 52 separate native physician pages. Match the physician API URL set to its official sitemap: a Physicians-only category filter loses a residency faculty role categorized Family Medicine. Use rich API city/state locations because JSON-LD Place.name may be Corporate, correct four clinic-city metadata mismatches from explicit titles, and enrich only description/date_posted with description_selector main .prose:not(.prose-invert) because JSON-LD descriptions are summaries while API body text loses HTML structure. Source schedule maps one part_time role; an explicit fully remote title supplies remote mode. Existing category scope should be reviewed if source taxonomy changes."
tags: ['algolia', 'workday', 'multiple-boards', 'json-ld', 'enrichment', 'location-quality']
---
# CoxHealth physician listings need API metadata and full page descriptions

## Setup
- Monitor: api_sniffer
- Scraper: json-ld

## Key decisions
- The official careers page links to Workday, but its physician link leads to a separate native board. The Workday monitor found 510 jobs; the native sitemap and API each contained the same 52 physician URLs on 2026-10-26.
- A `Physicians` category filter returned only 51 records. The residency faculty role uses `Family Medicine`; including that category restored exact sitemap parity. Retain native detail-URL validation and recheck parity if the site's taxonomy changes.
- JSON-LD descriptions were summaries, sometimes only a list of department labels. The API's full body was flattened text. Enrich only `description` and `date_posted` from the detail pages, replacing the summary with the full role container.
- JSON-LD locations sometimes preferred the facility name `Corporate`. Preserve monitor city/state locations during enrichment. Four family-medicine roles also publish Springfield in their API facility metadata despite explicit titles and full descriptions identifying Cassville, Marshfield, Republic, or Rogersville. Conditional field expressions corrected those four roles; retain the original city in metadata for review.
- Map the explicit source `Part Time` schedule to `part_time`. The remote-work source field is blank even for the fully remote radiology role, so its explicit title supplies `remote`.
- Ten random detail samples and four targeted clinic/remote/part-time samples contained complete HTML descriptions and posting dates. The boards had no overlap among stored URL samples. Home-health links were filtered views of the same Workday tenant; syndication mirrors were excluded.

## Config
The final monitor uses direct `api_sniffer` replay of the site's public Algolia request, scoped to the two verified categories, with strict native physician URL validation. Preserve the public request metadata discovered by the normal probe; do not copy private keys into examples.

Its field mappings join city and state, conditionally correct the four explicit clinic titles, map schedule values, and map the explicit remote title. The scraper configuration is:

```json
{
  "enrich": ["description", "date_posted"],
  "description_selector": "main .prose:not(.prose-invert)"
}
```

The raw scraper preview still shows the upstream JSON-LD locations. Those fields are outside the enrichment list; production retains the monitor's verified locations, employment type, and remote mode.

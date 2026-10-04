---
type: case-study
company: entegris
monitor: api_sniffer
scraper: embedded
summary: "Recruit CRM browser listing replay with static Flight HTML description enrichment"
tags: ['recruitcrm', 'rsc', 'enrich', 'browser-replay']
---
# Entegris — Recruit CRM description enrichment

## Setup
- Monitor: api_sniffer
- Scraper: embedded

## Key decisions
- Entegris's official India Technology Center links this GoAsia Talent board.
  Preserve its Entegris `companySlug`; it is an employer scope, not a country
  filter. The 18 jobs cover Pune and Kulim.
- Direct HTTP listing replay returned no `data.jobs`. Browser replay returned
  all 18 jobs through offset pagination. Keep `resource_policy: none`;
  resource blocking was not tested.
- The listing supplies titles and actual cities, but no descriptions. RSC
  extraction returned literal `$1b`/`$1c` references for `jdtext` and
  `jdtextdetails`. Nonempty field counts were misleading.
- Full descriptions are separate JSON arrays passed to `self.__next_f.push`.
  Parse their data without executing JavaScript. Role HTML starts with encoded
  paragraph or span markup. Include paragraphs with attributes; the exact
  `<p>` prefix missed four jobs. Exclude earlier consent/policy `<div>` chunks.
- Enrich descriptions only so monitor titles and locations survive. Applicant
  history fields for salary and employment are not job attributes.
- The final scraper extracted full HTML for all 18 jobs. Reviewed samples
  included responsibilities, requirements and explicit Entegris employer text,
  including Malaysia FOUP roles. `no_data` warnings remained even when the
  embedded extractor recovered the correct description; inspect the result.
- The 612-job global Workday inventory shared no URLs, titles or identities
  with this board. Keep both official sources.

## Config
The monitor replays the public POST endpoint
`https://albatross.recruitcrm.io/v1/external-pages/jobs-by-account/get` with
`browser: true` and `resource_policy: none`. Preserve query parameters
`account=GoAsiaTalent`, `batch=true`, and
`companySlug=17395194366120063211sWg`. Start with JSON body
`{"limit":10,"offset":0,"search_data":"","onlyJobs":true}`;
read `data.jobs`, paginate body `offset` by 10, and form canonical URLs with
`https://recruitcrm.io/apply/{slug}`. Map title from `name`. The tested location
expression `join(', ', [city, state, country][?@])` returned actual city values;
do not invent country defaults.

Static scraper configuration:

```json
{
  "pattern": "self\\.__next_f\\.push\\((?=\\[1,\"\\\\u003c(?:p(?:\\\\u003e| )|span ))",
  "fields": {"description": "[1]"},
  "enrich": ["description"]
}
```

Monitor time was about 5.2 seconds and static enrichment averaged 0.9 seconds
per job. Reverify current source shapes and employer scope before reuse.

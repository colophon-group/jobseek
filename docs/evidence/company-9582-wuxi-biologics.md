# WuXi Biologics resolver verification — issue #9582

Verified on 2026-10-28. This is one company, separate from WuXi AppTec.

## Company and branding

- [Official company profile](https://www.wuxibiologics.com/company/) describes
  biologics contract research, development and manufacturing and over 14,000
  employees. Industry: Pharmaceuticals & Biotech; employee bucket: 10,001+.
- The [2020 annual report](https://www.wuxibiologics.com/wp-content/uploads/2020-Annual-Report.pdf)
  dates the group's founding to May 2010, distinct from holding-company incorporation.
- English, German, French and Italian factual descriptions are included.
- Logo discovery selected the official transparent 373×100 SVG. The square
  Apple touch icon shows the same artwork with whitespace, so both committed
  image originals use the higher-quality SVG. Image CI supplies published URLs.

## Sources and coverage

| Configured source | Verified inventory | Extraction |
| --- | ---: | --- |
| Moka social site 99960 plus campus site 99961 | 709 active unique jobs | 521 social + 188 campus; 744 advertised records minus 35 inactive records; provider-ID union prevents duplicate publications |
| SuccessFactors `career55.sapsf.eu`, company `wuxibiolog` | 110 jobs | Two count-checked DWR pages; clean locations and 110 valid posting dates; static DOM description enrichment |
| Legacy SuccessFactors `career15.sapsf.cn`, company `WuXiBiologics` | 0 jobs | Validated zero counts and explicit “No jobs match your selections” information; retained as an official empty source |
| Worldwide employer LinkedIn, company ID `13290264` | 16 additional jobs | 182 raw jobs minus 166 in regions owned by the native feeds |

The LinkedIn remainder contains 13 Singapore roles and one role each in South
Korea, Japan and the UK. Its public employer URL and listing requests are
worldwide. The configuration excludes China, US, German and Irish location
values already covered by native sources, including US postal-state displays
and the observed Chinese metropolitan-area display. Other countries remain
eligible; there is no Singapore-only query or inclusion filter.

Ownership and additional-source research used the
[global careers page](https://www.wuxibiologics.com/join-us/),
[Chinese careers page](https://www.wuxibiologics.com.cn/join-us/),
[Irish careers site](https://www.wuxibiologicscareers.ie/),
[official campus board](https://job.wuxibiologics.com.cn/campus-recruitment/wuxibiologics/99961/),
and [employer LinkedIn page](https://www.linkedin.com/company/wuxibiologics/jobs/).
The current global careers page directs US, Irish and German applicants to
the same `career55` tenant. The older Chinese page also exposes the empty
`career15` tenant.

The corporate WordPress directory has 77 older entries, dated May 2026 or
earlier. Twenty-four normalized titles overlap the current SuccessFactors
inventory; sampled corporate “Apply now” links lead to the general `career55`
search rather than independent posting identities. The active ATS is the
authoritative source, so the older directory is not published as another board.

## Configuration exploration and fix

The seeded Moka monitor and its dedicated detail scraper worked without changes.
Both social and campus detail runs passed all required fields in ten samples each.

SuccessFactors probing suggested static navigation links and SAP library JSON,
neither of which represented jobs. Rendered DOM navigation to the legacy
`/career` route with `paginate_collect` found 110 real jobs, confirming the
cheaper native DWR monitor's complete inventory. No JobPosting JSON-LD or usable
embedded detail data was detected.

The native DWR path exposed three provider-format bugs:

1. `location_obj` values contained JSON display tuples such as
   `["Work Location",1,"Cranbury, NJ"]`, which were published as location text.
2. Posting language `en_GB` was incorrectly used to parse dates displayed in
   the listing/detail locale `en_US`; only 32 of 110 dates parsed before the fix.
3. The valid empty tenant callback includes a literal `info` string, which the
   restricted DWR grammar rejected.

The fix decodes the exact location tuple shape, uses the listing locale for
dates, and accepts only a literal informational callback value. Malformed
tuples, executable values, extra callback properties and inconsistent counts
still fail closed. No response JavaScript is executed.

## Validation

- Final runtime `monitor_one` verification: 835 jobs; all four source cycles
  completed without truncation.
- Current SuccessFactors samples in the US, Germany and Ireland have complete
  descriptions of 3,779–7,055 characters and clean title/location/date fields.
- LinkedIn detail samples cover Singapore, South Korea, Japan and the UK,
  with complete descriptions of 2,244–4,974 characters and full-time employment.
- CSV validation, browser capability census regeneration, Ruff lint and
  formatting checks pass. All 574 focused provider, registry and census tests
  pass; the changed runtime module passes Pyright.
- This resolver host lacks `/usr/local/bin/dom-detail-parse` and the Go
  toolchain. Local DOM sample verification used the repository's supported
  `DOM_GO_PARSE_ENABLED=0` Python reference extractor. That local selector is
  not persisted in board JSON; the production DOM config is unchanged.

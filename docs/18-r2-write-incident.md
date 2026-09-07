# R2 Class A write spike — incident report

**Investigation date:** 2026-09-07<br>
**Affected bucket:** `jobseek-assets`<br>
**Primary impact window:** 2026-08-11 through 2026-09-01<br>
**Status:** Root cause identified; incremental remediation tracked in
[#8454](https://github.com/colophon-group/jobseek/issues/8454)

## Executive summary

The primary culprit is the company Open Graph image prewarm added on August
11. A push changing any company-data CSV automatically enabled `--force`, so
roughly 20,000–22,000 company/locale objects were overwritten even when one
company changed.

Cloudflare Analytics reports 3,707,910 successful `PutObject` operations in
August, up from 1,272,880 in July: **+2,435,030 / +191.3% / 2.91×**. Company OG
writes account for **1,868,020 (76.7%)** of the increase. The jump begins on
August 11, when the prewarm feature shipped. GitHub recorded 90 successful
push-triggered prewarm runs in August.

At current Standard pricing, the observed successful writes are sufficient to
move the modeled Class A line from about **$4.50 to $13.50** after the free
tier and million-request rounding. Without company OG writes, August would
have remained in July's modeled billing tier. This is an estimate: Cloudflare
Analytics is not the billing system of record, and free usage is account-wide.

## Observed usage

Successful `PutObject` operations from Cloudflare's
`r2OperationsAdaptiveGroups` dataset:

| Prefix | July 2026 | August 2026 | Change | Share of increase |
|---|---:|---:|---:|---:|
| `og/company/**` | 11,570 | 1,879,590 | +1,868,020 | 76.7% |
| `job/**` | 1,259,990 | 1,826,640 | +566,650 | 23.3% |
| Other | 1,320 | 1,680 | +360 | <0.1% |
| **Total** | **1,272,880** | **3,707,910** | **+2,435,030** | **100%** |

From September 1–7, another 362,654 successful PUTs were reported: 93,331
company OG and 269,294 job-description writes. All but five company OG writes
occurred on September 1. The spike quieted, but the automatic force path still
made recurrence possible.

## Root cause

1. Commit [`3b52c78be`](https://github.com/colophon-group/jobseek/commit/3b52c78be)
   introduced company OG prewarming on August 11.
2. Pushes changing `companies.csv`, `company_descriptions.csv`, or
   `industries.csv` started the workflow.
3. The workflow promoted every such push to force mode.
4. Force mode ignored existing keys and uploaded every company × four locales.
5. Data-only changes reused the stable renderer namespace, so these were
   overwrites rather than new versioned objects.

The repository currently contains more than 5,600 companies. A forced run is
therefore about 22,700 Class A writes including markers and the site card. A
representative [August 27 run](https://github.com/colophon-group/jobseek/actions/runs/33039537246)
uploaded 22,060 cards for 5,515 companies.

## Remediation

Issue [#8454](https://github.com/colophon-group/jobseek/issues/8454) ships the
write-safety changes as one unit, avoiding an intermediate mode that could
publish a new marker over stale stable keys:

- completion markers use a revision-aware schema and record the exact target
  and last successfully published base revisions;
- automatic runs diff canonical rendered documents from that base to the
  target, so reordering and unused CSV fields cause no card PUTs;
- added/changed slugs render four locales, removals render nothing, and missing
  target keys are reconciled;
- legacy, malformed, mismatched, or non-ancestor marker lineage fails closed;
- the entire plan is checked against a 1,000-write automatic ceiling before
  the first PUT;
- every PUT attempt, including retries, site cards, and markers, consumes a
  separate 3,000-attempt runtime budget; SDK-internal retries are disabled;
- renderer concurrency is capped at four for automatic and manual runs;
- full rebuilds are manual-only, require an explicit confirmation phrase and
  Production approval, and remain capped at 30,000 planned writes / 90,000
  attempts;
- the immutable marker is published only after all cards succeed, followed by
  `current.json`;
- every CSV sync, including manual dispatch, launches and awaits the prewarm
  for its complete exact target snapshot before any production mutation;
- every crawler deployment does the same before its inline CSV publication;
- each handoff uses a one-time token so only its full prewarm run can satisfy
  the gate. A failed source revision therefore cannot hitchhike on a later
  unrelated data or crawler commit.

A one-company edit should produce four card PUTs plus at most two marker PUTs,
about a **99.97% reduction** from the former full-matrix behavior.

Alerts were intentionally excluded at the owner's request. Storage growth and
retention are being analyzed separately from operation usage.

## Secondary contributor: job descriptions

The `job/**` prefix increased by 566,650 writes in August. Sampled objects show
individual DuPont/Phenom job keys overwritten hundreds of times. The crawler's
deduplication hash includes title, locations, extras, metadata, dates, salary,
and job-type fields, while the R2 object contains only HTML. Metadata churn can
therefore trigger a PUT of unchanged object bytes. This is tracked separately
from the primary OG incident so its assumptions can be validated with
component-level diagnostics before changing deduplication semantics.

## Post-merge verification

1. Perform one approved bounded full rebuild to establish the schema-v2
   baseline.
2. Apply one controlled company-data change and confirm four card PUTs plus no
   more than two marker PUTs.
3. Verify all locales for changed/new/unchanged companies and confirm unrelated
   object modification times do not move.
4. Reconcile workflow `plannedWrites`, `putAttempts`, and `uploaded` fields with
   Cloudflare `og/company/**` PUTs after analytics ingestion.
5. Confirm production CSV sync used the same target revision recorded in the
   completion marker.

## Sources

- Cloudflare GraphQL Analytics, `r2OperationsAdaptiveGroups`, queried by UTC
  month, successful `PutObject`, bucket, storage class, and object-key prefix.
- GitHub Actions API and representative run logs.
- Cloudflare [R2 pricing](https://developers.cloudflare.com/r2/pricing/),
  [R2 metrics](https://developers.cloudflare.com/r2/platform/metrics-analytics/),
  and [GraphQL Analytics API](https://developers.cloudflare.com/analytics/graphql-api/).
